#!/bin/bash

# Real-Time Event Streaming Platform - Local Development Setup Script
# This script sets up the complete development environment locally

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="streaming-platform"
COMPOSE_FILE="infra/docker-compose/docker-compose.yml"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    local missing_deps=()
    
    if ! command_exists docker; then
        missing_deps+=("docker")
    fi
    
    if ! command_exists docker-compose; then
        missing_deps+=("docker-compose")
    fi
    
    if ! command_exists python3; then
        missing_deps+=("python3")
    fi
    
    if ! command_exists pip; then
        missing_deps+=("pip")
    fi
    
    if ! command_exists mvn; then
        print_warning "Maven not found. Required for building Flink jobs."
        missing_deps+=("maven")
    fi
    
    if ! command_exists java; then
        print_warning "Java not found. Required for Flink jobs."
        missing_deps+=("java")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        print_error "Missing dependencies: ${missing_deps[*]}"
        print_error "Please install the missing dependencies and run this script again."
        exit 1
    fi
    
    print_success "All prerequisites are satisfied!"
}

# Function to check Docker daemon
check_docker() {
    print_status "Checking Docker daemon..."
    
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker daemon is not running. Please start Docker and try again."
        exit 1
    fi
    
    print_success "Docker daemon is running!"
}

# Function to create necessary directories
create_directories() {
    print_status "Creating necessary directories..."
    
    directories=(
        "data/kafka"
        "data/zookeeper"
        "data/redis"
        "data/timescaledb"
        "data/elasticsearch"
        "data/prometheus"
        "data/grafana"
        "data/flink"
        "logs"
    )
    
    for dir in "${directories[@]}"; do
        mkdir -p "$dir"
        print_status "Created directory: $dir"
    done
    
    print_success "Directories created successfully!"
}

# Function to build services
build_services() {
    print_status "Building services..."
    
    # Build Java/Flink jobs
    if [ -d "streaming-jobs/aggregation-job" ]; then
        print_status "Building aggregation job..."
        cd streaming-jobs/aggregation-job
        mvn clean package -DskipTests
        cd ../../
        print_success "Aggregation job built successfully!"
    fi
    
    if [ -d "streaming-jobs/anomaly-detection" ]; then
        print_status "Building anomaly detection job..."
        cd streaming-jobs/anomaly-detection
        mvn clean package -DskipTests
        cd ../../
        print_success "Anomaly detection job built successfully!"
    fi
    
    # Build Docker images for Python services
    print_status "Building Docker images..."
    
    # Build read-api
    if [ -d "services/read-api" ]; then
        print_status "Building read-api image..."
        docker build -t streaming-platform/read-api:latest services/read-api/
        print_success "Read API image built successfully!"
    fi
    
    # Build alert-service
    if [ -d "services/alert-service" ]; then
        print_status "Building alert-service image..."
        docker build -t streaming-platform/alert-service:latest services/alert-service/
        print_success "Alert service image built successfully!"
    fi
    
    # Build event-producer
    if [ -d "ingestors/kafka-producer" ]; then
        print_status "Building event-producer image..."
        docker build -t streaming-platform/event-producer:latest ingestors/kafka-producer/
        print_success "Event producer image built successfully!"
    fi
}

# Function to start infrastructure
start_infrastructure() {
    print_status "Starting infrastructure services..."
    
    # Start core infrastructure first
    docker-compose -f $COMPOSE_FILE up -d zookeeper kafka redis timescaledb elasticsearch
    
    print_status "Waiting for infrastructure to be ready..."
    sleep 30
    
    # Check if Kafka is ready
    print_status "Checking Kafka readiness..."
    timeout=60
    while [ $timeout -gt 0 ]; do
        if docker-compose -f $COMPOSE_FILE exec -T kafka kafka-topics --bootstrap-server localhost:9092 --list >/dev/null 2>&1; then
            break
        fi
        sleep 2
        timeout=$((timeout - 2))
    done
    
    if [ $timeout -le 0 ]; then
        print_error "Kafka failed to start within timeout period"
        exit 1
    fi
    
    print_success "Core infrastructure is ready!"
}

# Function to create Kafka topics
create_kafka_topics() {
    print_status "Creating Kafka topics..."
    
    topics=(
        "events.v1:6:1"
        "alerts.v1:3:1"
        "aggregates.redis:3:1"
        "aggregates.db:3:1"
        "anomalies.db:3:1"
    )
    
    for topic_config in "${topics[@]}"; do
        IFS=':' read -r topic partitions replication <<< "$topic_config"
        
        print_status "Creating topic: $topic (partitions: $partitions, replication: $replication)"
        
        docker-compose -f $COMPOSE_FILE exec -T kafka kafka-topics \
            --bootstrap-server localhost:9092 \
            --create \
            --topic "$topic" \
            --partitions "$partitions" \
            --replication-factor "$replication" \
            --if-not-exists
    done
    
    print_success "Kafka topics created successfully!"
}

# Function to start processing services
start_processing_services() {
    print_status "Starting processing services..."
    
    # Start Flink cluster
    docker-compose -f $COMPOSE_FILE up -d flink-jobmanager flink-taskmanager
    
    print_status "Waiting for Flink to be ready..."
    sleep 20
    
    print_success "Processing services started!"
}

# Function to start monitoring services
start_monitoring() {
    print_status "Starting monitoring services..."
    
    docker-compose -f $COMPOSE_FILE up -d prometheus grafana alertmanager
    
    print_status "Waiting for monitoring services to be ready..."
    sleep 15
    
    print_success "Monitoring services started!"
}

# Function to start application services
start_application_services() {
    print_status "Starting application services..."
    
    # Start API services
    docker-compose -f $COMPOSE_FILE up -d read-api alert-service
    
    print_status "Waiting for application services to be ready..."
    sleep 10
    
    print_success "Application services started!"
}

# Function to start UI services
start_ui_services() {
    print_status "Starting UI services..."
    
    docker-compose -f $COMPOSE_FILE up -d kafka-ui
    
    print_success "UI services started!"
}

# Function to display service URLs
display_urls() {
    print_success "Setup complete! Services are available at:"
    echo ""
    echo -e "${BLUE}Core Services:${NC}"
    echo "  • Kafka UI:           http://localhost:8080"
    echo "  • Flink Dashboard:    http://localhost:8081"
    echo ""
    echo -e "${BLUE}APIs:${NC}"
    echo "  • Read API:           http://localhost:8000"
    echo "  • API Documentation:  http://localhost:8000/docs"
    echo "  • Alert Service:      http://localhost:8001"
    echo ""
    echo -e "${BLUE}Monitoring:${NC}"
    echo "  • Grafana:            http://localhost:3000 (admin/admin)"
    echo "  • Prometheus:         http://localhost:9090"
    echo "  • Alertmanager:       http://localhost:9093"
    echo ""
    echo -e "${BLUE}Databases:${NC}"
    echo "  • TimescaleDB:        localhost:5432 (admin/password)"
    echo "  • Redis:              localhost:6379"
    echo "  • Elasticsearch:      http://localhost:9200"
    echo ""
}

# Function to run health checks
run_health_checks() {
    print_status "Running health checks..."
    
    # Check API health
    if curl -s http://localhost:8000/health >/dev/null; then
        print_success "Read API is healthy"
    else
        print_warning "Read API health check failed"
    fi
    
    if curl -s http://localhost:8001/health >/dev/null; then
        print_success "Alert Service is healthy"
    else
        print_warning "Alert Service health check failed"
    fi
    
    # Check Grafana
    if curl -s http://localhost:3000 >/dev/null; then
        print_success "Grafana is accessible"
    else
        print_warning "Grafana is not accessible"
    fi
    
    print_success "Health checks completed!"
}

# Function to start event generation
start_event_generation() {
    print_status "Starting event generation..."
    
    if [ -f "ingestors/kafka-producer/event_producer.py" ]; then
        print_status "Starting event producer..."
        
        # Create Python virtual environment if it doesn't exist
        if [ ! -d "ingestors/kafka-producer/venv" ]; then
            cd ingestors/kafka-producer
            python3 -m venv venv
            source venv/bin/activate
            pip install -r requirements.txt
            cd ../../
        fi
        
        # Start event producer in background
        cd ingestors/kafka-producer
        source venv/bin/activate
        nohup python event_producer.py --rate 50 > ../../logs/event-producer.log 2>&1 &
        cd ../../
        
        print_success "Event producer started! (50 events/sec)"
        print_status "Event producer logs: logs/event-producer.log"
    else
        print_warning "Event producer not found, skipping..."
    fi
}

# Main setup function
main() {
    print_status "Starting Real-Time Event Streaming Platform setup..."
    echo ""
    
    check_prerequisites
    check_docker
    create_directories
    build_services
    start_infrastructure
    create_kafka_topics
    start_processing_services
    start_monitoring
    start_application_services
    start_ui_services
    
    print_status "Waiting for all services to be fully ready..."
    sleep 30
    
    run_health_checks
    display_urls
    
    echo ""
    print_status "To start generating events, run:"
    echo "  ./scripts/start-event-generation.sh"
    echo ""
    print_status "To stop all services, run:"
    echo "  docker-compose -f $COMPOSE_FILE down"
    echo ""
    print_status "To view logs, run:"
    echo "  docker-compose -f $COMPOSE_FILE logs -f [service-name]"
    echo ""
    
    print_success "Setup completed successfully! 🚀"
}

# Run main function
main "$@"
