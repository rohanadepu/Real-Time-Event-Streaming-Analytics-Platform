# Real-Time Event Streaming & Analytics Platform

A high-performance, scalable event streaming platform built with Apache Kafka, Apache Flink, and modern observability tools. This platform demonstrates enterprise-grade event processing capabilities with real-time analytics, anomaly detection, and comprehensive monitoring.

## 🎯 Goals

- **High-throughput ingestion**: Process and serve high-volume events with low latency
- **Scalability & Resilience**: Demonstrate robust microservices architecture on Kubernetes
- **Real-time Analytics**: Expose KPIs and anomaly alerts via APIs and dashboards
- **Observability**: Comprehensive monitoring, metrics, and alerting

## 📊 Success Metrics

- **Throughput**: Sustained ≥5,000 events/sec locally
- **Latency**: P95 read API latency ≤150ms for hot aggregates
- **Reliability**: Consumer crash recovery with no data loss
- **Detection Speed**: <2s detection-to-alert latency for outliers

## 🏗️ Architecture

```
Event Generator → Kafka (N partitions)
                      ↓
              Stream Processor (Flink)
             ↙        ↓        ↘
        Redis    TimescaleDB   Alerts Topic
           ↓           ↓           ↓
       Read API    Read API   Alert Service
           ↘_________↓___________↙
                 Grafana + Web UI
```

## 🛠️ Technology Stack

All tools used are **100% free and open-source**:

### Core Infrastructure
- **Container Runtime**: Docker & Kubernetes (kind/minikube)
- **Message Broker**: Apache Kafka + Zookeeper
- **Stream Processing**: Apache Flink
- **Hot Storage**: Redis
- **Cold Storage**: TimescaleDB (PostgreSQL extension)
- **Search**: Elasticsearch (optional)

### APIs & Frontend
- **Backend API**: FastAPI (Python)
- **Frontend**: React with Next.js (optional)
- **API Documentation**: FastAPI's built-in Swagger UI

### Observability & Monitoring
- **Metrics**: Prometheus
- **Visualization**: Grafana
- **Alerting**: Alertmanager
- **Distributed Tracing**: Jaeger (optional)

### Development & Testing
- **CI/CD**: GitHub Actions
- **Load Testing**: k6
- **Infrastructure as Code**: Helm charts
- **Documentation**: Markdown with MkDocs

## 📁 Project Structure

```
real-time-streaming-platform/
├── ingestors/                 # Event generators and producers
│   ├── kafka-producer/        # Python Kafka producer
│   └── event-generator/       # Synthetic event generation
├── streaming-jobs/            # Flink streaming applications
│   ├── aggregation-job/       # Real-time aggregations
│   └── anomaly-detection/     # Anomaly detection pipeline
├── services/                  # Microservices
│   ├── read-api/             # FastAPI backend
│   ├── alert-service/        # Alert handling service
│   └── frontend/             # React dashboard (optional)
├── infra/                    # Infrastructure configurations
│   ├── docker-compose/       # Local development setup
│   ├── k8s/                  # Kubernetes manifests
│   └── helm/                 # Helm charts
├── observability/            # Monitoring and observability
│   ├── prometheus/           # Prometheus configuration
│   ├── grafana/             # Grafana dashboards
│   └── alerting/            # Alert rules and configurations
├── loadtests/               # Performance testing
│   ├── k6-scripts/          # k6 load test scripts
│   └── scenarios/           # Test scenarios
├── docs/                    # Documentation
├── scripts/                 # Utility scripts
├── .github/                 # GitHub Actions workflows
├── README.md               # This file
├── SECURITY.md            # Security considerations
├── COST.md               # Cost analysis and optimization
└── .gitignore            # Git ignore rules
```

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Kubernetes (kind/minikube/Docker Desktop)
- Python 3.9+
- Node.js 16+ (for frontend)
- k6 (for load testing)

### Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/Real-Time-Event-Streaming-Analytics-Platform.git
   cd Real-Time-Event-Streaming-Analytics-Platform
   ```

2. **Start the infrastructure**
   ```bash
   cd infra/docker-compose
   docker-compose up -d
   ```

3. **Deploy streaming jobs**
   ```bash
   cd streaming-jobs
   ./deploy-local.sh
   ```

4. **Start the API services**
   ```bash
   cd services/read-api
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn main:app --reload
   ```

5. **Access the dashboards**
   - Grafana: http://localhost:3000 (admin/admin)
   - API Documentation: http://localhost:8000/docs
   - Kafka UI: http://localhost:8080

### Kubernetes Deployment

1. **Create kind cluster**
   ```bash
   kind create cluster --config infra/k8s/kind-config.yaml
   ```

2. **Deploy with Helm**
   ```bash
   helm install streaming-platform infra/helm/streaming-platform
   ```

## 📈 Data Model

### Event Schema
```json
{
  "event_id": "uuid",
  "source": "web|device|service-x",
  "timestamp": "2025-08-14T16:02:00Z",
  "attributes": {
    "user_id": "u123",
    "metric": 42.7,
    "status": "ok"
  }
}
```

### Hot Aggregate (Redis)
```
Key: agg:{source}:{window}:{timestamp}
Value: {
  "count": 1234,
  "p95_metric": 57.1,
  "error_rate": 0.02,
  "avg_metric": 45.3
}
```

### Cold Storage (TimescaleDB)
```sql
CREATE TABLE events_raw (
  ts TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL,
  metric DOUBLE PRECISION,
  status TEXT,
  user_id TEXT,
  event_id UUID
);

SELECT create_hypertable('events_raw', 'ts');
CREATE INDEX ON events_raw (source, ts DESC);
```

## 🔍 API Endpoints

### KPI Endpoints
- `GET /kpi?source=web&window=1m` - Hot aggregates from Redis
- `GET /series?source=web&from=...&to=...` - Historical data from TimescaleDB
- `GET /alerts?since=...` - Recent anomaly events

### Health & Monitoring
- `GET /health` - Service health check
- `GET /metrics` - Prometheus metrics
- `GET /ready` - Readiness probe

## 🧪 Testing

### Unit Tests
```bash
pytest tests/unit/
```

### Integration Tests
```bash
pytest tests/integration/
```

### Load Testing
```bash
cd loadtests
k6 run scenarios/high-throughput.js
```

## 📊 Monitoring & Observability

### Metrics Collected
- **Kafka**: Producer/consumer throughput, lag, partition metrics
- **Flink**: Checkpoint duration, backpressure, task metrics
- **API**: Request latency, error rates, throughput
- **Infrastructure**: CPU, memory, disk, network usage

### Dashboards
- **Event Pipeline Overview**: End-to-end event flow metrics
- **Kafka Monitoring**: Broker health, topic metrics, consumer lag
- **Flink Jobs**: Job health, checkpointing, parallelism
- **API Performance**: Latency histograms, error rates, SLA tracking
- **Infrastructure**: Resource utilization, capacity planning

### Alerting Rules
- High consumer lag (>1000 messages)
- API error rate >5%
- Flink job failures or restarts
- Resource utilization >80%
- Anomaly detection alerts

## 🔒 Security Considerations

See [SECURITY.md](SECURITY.md) for detailed security guidelines including:
- Network isolation and segmentation
- Authentication and authorization
- Data encryption at rest and in transit
- Container security best practices
- Secrets management

## 💰 Cost Optimization

See [COST.md](COST.md) for cost analysis and optimization strategies including:
- Resource right-sizing
- Auto-scaling configurations
- Storage optimization
- Network cost reduction

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 Related Projects

- [Apache Kafka](https://kafka.apache.org/)
- [Apache Flink](https://flink.apache.org/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Prometheus](https://prometheus.io/)
- [Grafana](https://grafana.com/)

## 📞 Support

- 📫 Create an issue for bug reports or feature requests
- 💬 Join discussions in the [GitHub Discussions](https://github.com/your-username/Real-Time-Event-Streaming-Analytics-Platform/discussions)
- 📖 Check the [documentation](docs/) for detailed guides