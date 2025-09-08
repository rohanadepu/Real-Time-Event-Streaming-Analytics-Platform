from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import asyncio
import json
import logging
from kafka import KafkaConsumer
from kafka.errors import KafkaError
import psycopg2
from psycopg2.extras import RealDictCursor
import smtplib
from email.mime.text import MimeText
from email.mime.multipart import MimeMultipart
import requests
import os
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import threading
import signal
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prometheus metrics
ALERTS_PROCESSED = Counter('alerts_processed_total', 'Total alerts processed', ['severity', 'source'])
NOTIFICATIONS_SENT = Counter('notifications_sent_total', 'Total notifications sent', ['channel', 'status'])
ALERT_PROCESSING_TIME = Histogram('alert_processing_seconds', 'Time spent processing alerts')
ACTIVE_ALERTS = Gauge('active_alerts', 'Number of active alerts', ['severity'])

# Pydantic models
class AlertPayload(BaseModel):
    alert_id: str
    source: str
    timestamp: datetime
    anomaly_type: str
    severity: str
    value: float
    threshold: float
    z_score: float
    description: str
    is_anomaly: bool
    stats: Optional[Dict[str, float]] = None

class WebhookAlert(BaseModel):
    receiver: str
    status: str
    alerts: List[Dict[str, Any]]
    groupLabels: Dict[str, str]
    commonLabels: Dict[str, str]
    commonAnnotations: Dict[str, str]
    externalURL: str
    version: str
    groupKey: str

class NotificationConfig(BaseModel):
    email_enabled: bool = True
    slack_enabled: bool = False
    webhook_enabled: bool = True
    email_recipients: List[str] = []
    slack_webhook_url: Optional[str] = None
    custom_webhooks: List[str] = []

class AlertRule(BaseModel):
    name: str
    severity_threshold: str
    sources: List[str] = []  # Empty means all sources
    cooldown_minutes: int = 5
    enabled: bool = True

# Alert service class
class AlertService:
    def __init__(self):
        self.config = NotificationConfig()
        self.alert_rules = self._load_alert_rules()
        self.recent_alerts = {}  # For cooldown tracking
        self.running = False
        
        # Database configuration
        self.db_config = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', 5432)),
            'dbname': os.getenv('POSTGRES_DB', 'streaming_analytics'),
            'user': os.getenv('POSTGRES_USER', 'admin'),
            'password': os.getenv('POSTGRES_PASSWORD', 'password')
        }
        
        # Kafka configuration
        self.kafka_config = {
            'bootstrap_servers': [os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')],
            'group_id': 'alert-service',
            'auto_offset_reset': 'latest',
            'enable_auto_commit': True,
            'value_deserializer': lambda x: json.loads(x.decode('utf-8'))
        }
    
    def _load_alert_rules(self) -> List[AlertRule]:
        """Load alert rules from configuration"""
        default_rules = [
            AlertRule(
                name="Critical Anomalies",
                severity_threshold="critical",
                cooldown_minutes=1
            ),
            AlertRule(
                name="Warning Anomalies",
                severity_threshold="warning",
                cooldown_minutes=5
            ),
            AlertRule(
                name="High Throughput Sources",
                severity_threshold="warning",
                sources=["web", "api"],
                cooldown_minutes=10
            )
        ]
        return default_rules
    
    async def start_kafka_consumer(self):
        """Start Kafka consumer in background thread"""
        def consume_alerts():
            consumer = KafkaConsumer('alerts.v1', **self.kafka_config)
            logger.info("Started Kafka consumer for alerts")
            
            try:
                for message in consumer:
                    if not self.running:
                        break
                    
                    try:
                        alert_data = message.value
                        alert = AlertPayload(**alert_data)
                        asyncio.run(self.process_alert(alert))
                    except Exception as e:
                        logger.error(f"Error processing alert message: {e}")
                        
            except Exception as e:
                logger.error(f"Kafka consumer error: {e}")
            finally:
                consumer.close()
        
        self.running = True
        consumer_thread = threading.Thread(target=consume_alerts, daemon=True)
        consumer_thread.start()
        logger.info("Kafka consumer thread started")
    
    async def process_alert(self, alert: AlertPayload):
        """Process incoming alert and trigger notifications if needed"""
        with ALERT_PROCESSING_TIME.time():
            try:
                # Record metrics
                ALERTS_PROCESSED.labels(severity=alert.severity, source=alert.source).inc()
                
                # Check if this alert should trigger notifications
                if await self._should_notify(alert):
                    # Store alert in database
                    await self._store_alert(alert)
                    
                    # Send notifications
                    await self._send_notifications(alert)
                    
                    # Update active alerts gauge
                    self._update_active_alerts_metric()
                
                logger.info(f"Processed alert: {alert.alert_id} from {alert.source} - {alert.severity}")
                
            except Exception as e:
                logger.error(f"Error processing alert {alert.alert_id}: {e}")
    
    async def _should_notify(self, alert: AlertPayload) -> bool:
        """Determine if alert should trigger notifications based on rules and cooldowns"""
        # Only notify for actual anomalies
        if not alert.is_anomaly:
            return False
        
        # Check against alert rules
        applicable_rules = [
            rule for rule in self.alert_rules 
            if rule.enabled and 
            (not rule.sources or alert.source in rule.sources) and
            rule.severity_threshold == alert.severity
        ]
        
        if not applicable_rules:
            return False
        
        # Check cooldown
        cooldown_key = f"{alert.source}:{alert.severity}"
        current_time = datetime.utcnow()
        
        if cooldown_key in self.recent_alerts:
            time_since_last = current_time - self.recent_alerts[cooldown_key]
            min_cooldown = min(rule.cooldown_minutes for rule in applicable_rules)
            
            if time_since_last < timedelta(minutes=min_cooldown):
                return False
        
        # Update cooldown tracking
        self.recent_alerts[cooldown_key] = current_time
        return True
    
    async def _store_alert(self, alert: AlertPayload):
        """Store alert in database"""
        try:
            conn = psycopg2.connect(**self.db_config, cursor_factory=RealDictCursor)
            
            with conn.cursor() as cur:
                insert_query = """
                    INSERT INTO anomalies (ts, source, anomaly_type, severity, value, threshold, z_score, description)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
                cur.execute(insert_query, (
                    alert.timestamp,
                    alert.source,
                    alert.anomaly_type,
                    alert.severity,
                    alert.value,
                    alert.threshold,
                    alert.z_score,
                    alert.description
                ))
                conn.commit()
            
            conn.close()
            logger.debug(f"Stored alert {alert.alert_id} in database")
            
        except Exception as e:
            logger.error(f"Error storing alert in database: {e}")
    
    async def _send_notifications(self, alert: AlertPayload):
        """Send notifications through configured channels"""
        tasks = []
        
        if self.config.email_enabled and self.config.email_recipients:
            tasks.append(self._send_email_notification(alert))
        
        if self.config.slack_enabled and self.config.slack_webhook_url:
            tasks.append(self._send_slack_notification(alert))
        
        if self.config.webhook_enabled and self.config.custom_webhooks:
            for webhook_url in self.config.custom_webhooks:
                tasks.append(self._send_webhook_notification(alert, webhook_url))
        
        # Execute all notification tasks concurrently
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_email_notification(self, alert: AlertPayload):
        """Send email notification"""
        try:
            subject = f"🚨 {alert.severity.upper()} Alert: {alert.source}"
            
            body = f"""
            Alert Details:
            - Source: {alert.source}
            - Severity: {alert.severity}
            - Type: {alert.anomaly_type}
            - Value: {alert.value:.2f}
            - Threshold: {alert.threshold:.2f}
            - Z-Score: {alert.z_score:.2f}
            - Time: {alert.timestamp}
            - Description: {alert.description}
            
            Statistics:
            {json.dumps(alert.stats, indent=2) if alert.stats else 'N/A'}
            """
            
            # Note: In production, configure actual SMTP settings
            logger.info(f"Would send email notification for alert {alert.alert_id}")
            NOTIFICATIONS_SENT.labels(channel='email', status='success').inc()
            
        except Exception as e:
            logger.error(f"Error sending email notification: {e}")
            NOTIFICATIONS_SENT.labels(channel='email', status='error').inc()
    
    async def _send_slack_notification(self, alert: AlertPayload):
        """Send Slack notification"""
        try:
            color = {
                'critical': '#FF0000',
                'warning': '#FFA500',
                'info': '#00FF00'
            }.get(alert.severity, '#808080')
            
            payload = {
                "attachments": [{
                    "color": color,
                    "title": f"{alert.severity.upper()} Alert: {alert.source}",
                    "text": alert.description,
                    "fields": [
                        {"title": "Value", "value": f"{alert.value:.2f}", "short": True},
                        {"title": "Z-Score", "value": f"{alert.z_score:.2f}", "short": True},
                        {"title": "Time", "value": alert.timestamp.isoformat(), "short": False}
                    ],
                    "timestamp": int(alert.timestamp.timestamp())
                }]
            }
            
            # Note: Replace with actual Slack webhook implementation
            logger.info(f"Would send Slack notification for alert {alert.alert_id}")
            NOTIFICATIONS_SENT.labels(channel='slack', status='success').inc()
            
        except Exception as e:
            logger.error(f"Error sending Slack notification: {e}")
            NOTIFICATIONS_SENT.labels(channel='slack', status='error').inc()
    
    async def _send_webhook_notification(self, alert: AlertPayload, webhook_url: str):
        """Send webhook notification"""
        try:
            payload = {
                "alert_id": alert.alert_id,
                "source": alert.source,
                "severity": alert.severity,
                "description": alert.description,
                "timestamp": alert.timestamp.isoformat(),
                "value": alert.value,
                "z_score": alert.z_score,
                "stats": alert.stats
            }
            
            # Note: In production, use aiohttp for async HTTP requests
            logger.info(f"Would send webhook notification to {webhook_url} for alert {alert.alert_id}")
            NOTIFICATIONS_SENT.labels(channel='webhook', status='success').inc()
            
        except Exception as e:
            logger.error(f"Error sending webhook notification: {e}")
            NOTIFICATIONS_SENT.labels(channel='webhook', status='error').inc()
    
    def _update_active_alerts_metric(self):
        """Update Prometheus gauge for active alerts"""
        try:
            conn = psycopg2.connect(**self.db_config, cursor_factory=RealDictCursor)
            
            with conn.cursor() as cur:
                # Count active alerts by severity
                cur.execute("""
                    SELECT severity, COUNT(*) as count 
                    FROM anomalies 
                    WHERE resolved = false 
                    AND ts > NOW() - INTERVAL '1 hour'
                    GROUP BY severity
                """)
                
                results = cur.fetchall()
                
                # Reset gauges
                for severity in ['critical', 'warning', 'info']:
                    ACTIVE_ALERTS.labels(severity=severity).set(0)
                
                # Set current values
                for row in results:
                    ACTIVE_ALERTS.labels(severity=row['severity']).set(row['count'])
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating active alerts metric: {e}")
    
    def stop(self):
        """Stop the alert service"""
        self.running = False
        logger.info("Alert service stopped")

# Global alert service instance
alert_service = AlertService()

# FastAPI app
app = FastAPI(
    title="Alert Service",
    description="Real-time alert processing and notification service",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Start background services"""
    await alert_service.start_kafka_consumer()
    
    # Start Prometheus metrics server
    start_http_server(9090)
    logger.info("Prometheus metrics server started on port 9090")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    alert_service.stop()

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "alert-service",
        "timestamp": datetime.utcnow(),
        "version": "1.0.0"
    }

@app.post("/webhook/alerts")
async def receive_alertmanager_webhook(payload: WebhookAlert):
    """Receive webhook from Alertmanager"""
    try:
        logger.info(f"Received Alertmanager webhook: {payload.status}")
        
        for alert_data in payload.alerts:
            # Process Alertmanager alert format
            alert = AlertPayload(
                alert_id=alert_data.get('fingerprint', 'unknown'),
                source=alert_data.get('labels', {}).get('instance', 'unknown'),
                timestamp=datetime.utcnow(),
                anomaly_type='infrastructure',
                severity=alert_data.get('labels', {}).get('severity', 'warning'),
                value=0.0,
                threshold=0.0,
                z_score=0.0,
                description=alert_data.get('annotations', {}).get('summary', 'Infrastructure alert'),
                is_anomaly=True
            )
            
            await alert_service.process_alert(alert)
        
        return {"status": "success", "processed": len(payload.alerts)}
        
    except Exception as e:
        logger.error(f"Error processing Alertmanager webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook/critical")
async def receive_critical_alerts(payload: Dict[str, Any]):
    """Receive critical alerts via webhook"""
    logger.info(f"Received critical alert webhook: {payload}")
    return {"status": "received", "severity": "critical"}

@app.post("/webhook/warning")
async def receive_warning_alerts(payload: Dict[str, Any]):
    """Receive warning alerts via webhook"""
    logger.info(f"Received warning alert webhook: {payload}")
    return {"status": "received", "severity": "warning"}

@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/alerts/stats")
async def get_alert_stats():
    """Get alert statistics"""
    try:
        conn = psycopg2.connect(**alert_service.db_config, cursor_factory=RealDictCursor)
        
        with conn.cursor() as cur:
            # Get recent alert counts
            cur.execute("""
                SELECT 
                    severity,
                    COUNT(*) as total,
                    COUNT(CASE WHEN resolved = false THEN 1 END) as active,
                    COUNT(CASE WHEN ts > NOW() - INTERVAL '1 hour' THEN 1 END) as last_hour
                FROM anomalies 
                WHERE ts > NOW() - INTERVAL '24 hours'
                GROUP BY severity
            """)
            
            stats = cur.fetchall()
        
        conn.close()
        
        return {
            "timestamp": datetime.utcnow(),
            "stats": [dict(row) for row in stats]
        }
        
    except Exception as e:
        logger.error(f"Error getting alert stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Signal handlers for graceful shutdown
def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    alert_service.stop()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
