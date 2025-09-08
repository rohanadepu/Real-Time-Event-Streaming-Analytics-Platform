from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import redis
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import logging
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import start_http_server
import os
from contextlib import asynccontextmanager
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prometheus metrics
REQUEST_COUNT = Counter('api_requests_total', 'Total API requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('api_request_duration_seconds', 'API request duration', ['method', 'endpoint'])
ACTIVE_CONNECTIONS = Gauge('api_active_connections', 'Active database connections')
CACHE_HITS = Counter('api_cache_hits_total', 'Cache hits', ['cache_type'])
CACHE_MISSES = Counter('api_cache_misses_total', 'Cache misses', ['cache_type'])

# Pydantic models
class TimeRange(BaseModel):
    start: datetime
    end: datetime

class KPIRequest(BaseModel):
    source: Optional[str] = None
    window: str = Field(default="1m", description="Time window: 1m, 5m, 15m, 1h")
    
class SeriesRequest(BaseModel):
    source: Optional[str] = None
    start_time: datetime = Field(alias="from")
    end_time: datetime = Field(alias="to")
    aggregation: Optional[str] = Field(default="avg", description="Aggregation: avg, sum, count, p95")

class AlertsRequest(BaseModel):
    since: Optional[datetime] = None
    resolved: Optional[bool] = None
    severity: Optional[str] = None

class KPIResponse(BaseModel):
    source: str
    window: str
    timestamp: datetime
    count: int
    avg_metric: float
    p95_metric: float
    error_rate: float

class TimeSeriesPoint(BaseModel):
    timestamp: datetime
    value: float

class SeriesResponse(BaseModel):
    source: str
    metric: str
    data: List[TimeSeriesPoint]

class Alert(BaseModel):
    id: int
    timestamp: datetime
    source: str
    anomaly_type: str
    severity: str
    value: float
    threshold: float
    z_score: float
    description: str
    resolved: bool

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    services: Dict[str, str]

# Database and cache connections
class DatabaseManager:
    def __init__(self):
        self.redis_client = None
        self.pg_pool = None
    
    async def connect(self):
        """Initialize connections"""
        try:
            # Redis connection
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                db=0,
                decode_responses=True
            )
            
            # Test Redis connection
            self.redis_client.ping()
            logger.info("Connected to Redis")
            
            # PostgreSQL connection parameters
            self.pg_params = {
                'host': os.getenv('POSTGRES_HOST', 'localhost'),
                'port': int(os.getenv('POSTGRES_PORT', 5432)),
                'dbname': os.getenv('POSTGRES_DB', 'streaming_analytics'),
                'user': os.getenv('POSTGRES_USER', 'admin'),
                'password': os.getenv('POSTGRES_PASSWORD', 'password')
            }
            
            # Test PostgreSQL connection
            conn = psycopg2.connect(**self.pg_params)
            conn.close()
            logger.info("Connected to PostgreSQL")
            
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            raise
    
    def get_redis(self):
        return self.redis_client
    
    def get_pg_connection(self):
        return psycopg2.connect(**self.pg_params, cursor_factory=RealDictCursor)

# Global database manager
db_manager = DatabaseManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db_manager.connect()
    yield
    # Shutdown
    if db_manager.redis_client:
        db_manager.redis_client.close()

# FastAPI app
app = FastAPI(
    title="Real-Time Event Streaming Analytics API",
    description="API for querying real-time event analytics and KPIs",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency for database connections
def get_redis() -> redis.Redis:
    return db_manager.get_redis()

def get_pg_connection():
    return db_manager.get_pg_connection()

# Utility functions
def parse_window(window: str) -> int:
    """Parse window string to seconds"""
    window_map = {
        '1m': 60,
        '5m': 300,
        '15m': 900,
        '1h': 3600,
        '1d': 86400
    }
    return window_map.get(window, 60)

def build_cache_key(prefix: str, **kwargs) -> str:
    """Build Redis cache key"""
    key_parts = [prefix]
    for k, v in sorted(kwargs.items()):
        if v is not None:
            key_parts.append(f"{k}:{v}")
    return ":".join(key_parts)

# API Routes
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    services = {}
    
    try:
        # Check Redis
        db_manager.redis_client.ping()
        services["redis"] = "healthy"
    except Exception as e:
        services["redis"] = f"unhealthy: {str(e)}"
    
    try:
        # Check PostgreSQL
        conn = db_manager.get_pg_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        services["postgresql"] = "healthy"
    except Exception as e:
        services["postgresql"] = f"unhealthy: {str(e)}"
    
    status = "healthy" if all(s == "healthy" for s in services.values()) else "unhealthy"
    
    return HealthResponse(
        status=status,
        timestamp=datetime.utcnow(),
        services=services
    )

@app.get("/ready")
async def readiness_check():
    """Readiness probe for Kubernetes"""
    try:
        # Quick health checks
        db_manager.redis_client.ping()
        conn = db_manager.get_pg_connection()
        conn.close()
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service not ready: {str(e)}")

@app.get("/kpi", response_model=List[KPIResponse])
async def get_kpi(
    source: Optional[str] = Query(None, description="Event source filter"),
    window: str = Query("1m", description="Time window: 1m, 5m, 15m, 1h"),
    redis_client: redis.Redis = Depends(get_redis)
):
    """Get real-time KPI data from hot storage (Redis)"""
    
    with REQUEST_DURATION.labels(method="GET", endpoint="/kpi").time():
        try:
            window_seconds = parse_window(window)
            current_time = datetime.utcnow()
            
            # Build cache key pattern
            if source:
                pattern = f"agg:{source}:{window}:*"
            else:
                pattern = f"agg:*:{window}:*"
            
            # Get keys from Redis
            keys = redis_client.keys(pattern)
            
            if not keys:
                CACHE_MISSES.labels(cache_type="kpi").inc()
                return []
            
            CACHE_HITS.labels(cache_type="kpi").inc()
            
            # Get values
            pipeline = redis_client.pipeline()
            for key in keys:
                pipeline.get(key)
            values = pipeline.execute()
            
            # Parse results
            results = []
            for key, value in zip(keys, values):
                if value:
                    try:
                        data = json.loads(value)
                        key_parts = key.split(':')
                        
                        results.append(KPIResponse(
                            source=key_parts[1],
                            window=key_parts[2],
                            timestamp=datetime.fromisoformat(key_parts[3].replace('Z', '+00:00')),
                            count=data.get('count', 0),
                            avg_metric=data.get('avg_metric', 0.0),
                            p95_metric=data.get('p95_metric', 0.0),
                            error_rate=data.get('error_rate', 0.0)
                        ))
                    except (json.JSONDecodeError, IndexError, ValueError) as e:
                        logger.warning(f"Failed to parse KPI data for key {key}: {e}")
            
            # Sort by timestamp (most recent first)
            results.sort(key=lambda x: x.timestamp, reverse=True)
            
            REQUEST_COUNT.labels(method="GET", endpoint="/kpi", status="success").inc()
            return results[:100]  # Limit results
            
        except Exception as e:
            REQUEST_COUNT.labels(method="GET", endpoint="/kpi", status="error").inc()
            logger.error(f"Error getting KPI data: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/series", response_model=List[SeriesResponse])
async def get_series(
    source: Optional[str] = Query(None, description="Event source filter"),
    start_time: datetime = Query(alias="from", description="Start time (ISO format)"),
    end_time: datetime = Query(alias="to", description="End time (ISO format)"),
    aggregation: str = Query("avg", description="Aggregation: avg, sum, count, p95")
):
    """Get historical time series data from cold storage (TimescaleDB)"""
    
    with REQUEST_DURATION.labels(method="GET", endpoint="/series").time():
        try:
            conn = db_manager.get_pg_connection()
            
            # Build query based on aggregation
            agg_map = {
                'avg': 'AVG(metric)',
                'sum': 'SUM(metric)',
                'count': 'COUNT(*)',
                'p95': 'PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY metric)'
            }
            
            agg_func = agg_map.get(aggregation, 'AVG(metric)')
            
            # Base query
            query = f"""
                SELECT 
                    source,
                    date_trunc('minute', ts) as bucket,
                    {agg_func} as value
                FROM events_raw 
                WHERE ts >= %s AND ts <= %s
            """
            params = [start_time, end_time]
            
            if source:
                query += " AND source = %s"
                params.append(source)
            
            query += """
                GROUP BY source, bucket
                ORDER BY source, bucket
            """
            
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
            
            conn.close()
            
            # Group by source
            series_data = {}
            for row in rows:
                source_name = row['source']
                if source_name not in series_data:
                    series_data[source_name] = []
                
                series_data[source_name].append(TimeSeriesPoint(
                    timestamp=row['bucket'],
                    value=float(row['value']) if row['value'] is not None else 0.0
                ))
            
            # Convert to response format
            results = [
                SeriesResponse(
                    source=source_name,
                    metric=aggregation,
                    data=data_points
                )
                for source_name, data_points in series_data.items()
            ]
            
            REQUEST_COUNT.labels(method="GET", endpoint="/series", status="success").inc()
            return results
            
        except Exception as e:
            REQUEST_COUNT.labels(method="GET", endpoint="/series", status="error").inc()
            logger.error(f"Error getting series data: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/alerts", response_model=List[Alert])
async def get_alerts(
    since: Optional[datetime] = Query(None, description="Get alerts since this time"),
    resolved: Optional[bool] = Query(None, description="Filter by resolved status"),
    severity: Optional[str] = Query(None, description="Filter by severity")
):
    """Get anomaly alerts"""
    
    with REQUEST_DURATION.labels(method="GET", endpoint="/alerts").time():
        try:
            conn = db_manager.get_pg_connection()
            
            # Build query
            query = "SELECT * FROM anomalies WHERE 1=1"
            params = []
            
            if since:
                query += " AND ts >= %s"
                params.append(since)
            
            if resolved is not None:
                query += " AND resolved = %s"
                params.append(resolved)
            
            if severity:
                query += " AND severity = %s"
                params.append(severity)
            
            query += " ORDER BY ts DESC LIMIT 1000"
            
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
            
            conn.close()
            
            # Convert to response format
            alerts = [
                Alert(
                    id=row['id'],
                    timestamp=row['ts'],
                    source=row['source'],
                    anomaly_type=row['anomaly_type'],
                    severity=row['severity'],
                    value=float(row['value']) if row['value'] is not None else 0.0,
                    threshold=float(row['threshold']) if row['threshold'] is not None else 0.0,
                    z_score=float(row['z_score']) if row['z_score'] is not None else 0.0,
                    description=row['description'] or "",
                    resolved=row['resolved']
                )
                for row in rows
            ]
            
            REQUEST_COUNT.labels(method="GET", endpoint="/alerts", status="success").inc()
            return alerts
            
        except Exception as e:
            REQUEST_COUNT.labels(method="GET", endpoint="/alerts", status="error").inc()
            logger.error(f"Error getting alerts: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
