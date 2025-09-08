#!/bin/bash

# Real-Time Event Streaming Platform - System Status Check
# This script verifies that all components are running correctly

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="infra/docker-compose/docker-compose.yml"
BASE_URL="http://localhost:8000"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Function to check if service is running
check_service() {
    local service_name=$1
    local expected_status="running"
    
    local status=$(docker-compose -f $COMPOSE_FILE ps -q $service_name 2>/dev/null | xargs docker inspect -f '{{.State.Status}}' 2>/dev/null || echo "not found")
    
    if [ "$status" = "$expected_status" ]; then
        print_success "$service_name is running"
        return 0
    else
        print_error "$service_name is not running (status: $status)"
        return 1
    fi
}

# Function to check HTTP endpoint
check_http_endpoint() {
    local name=$1
    local url=$2
    local expected_status=${3:-200}
    
    local response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    
    if [ "$response" = "$expected_status" ]; then
        print_success "$name is accessible ($url)"
        return 0
    else
        print_error "$name is not accessible ($url) - HTTP $response"
        return 1
    fi
}

# Function to check database connectivity
check_database() {
    local result=$(docker-compose -f $COMPOSE_FILE exec -T timescaledb psql -U admin -d streaming_analytics -c "SELECT 1;" 2>/dev/null || echo "failed")
    
    if [[ $result == *"1"* ]]; then
        print_success "TimescaleDB is accessible"
        return 0
    else
        print_error "TimescaleDB is not accessible"
        return 1
    fi
}

# Function to check Redis connectivity
check_redis() {
    local result=$(docker-compose -f $COMPOSE_FILE exec -T redis redis-cli ping 2>/dev/null || echo "failed")
    
    if [ "$result" = "PONG" ]; then
        print_success "Redis is accessible"
        return 0
    else
        print_error "Redis is not accessible"
        return 1
    fi
}

# Function to check Kafka
check_kafka() {
    local result=$(docker-compose -f $COMPOSE_FILE exec -T kafka kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null | wc -l)
    
    if [ "$result" -gt 0 ]; then
        print_success "Kafka is accessible and has topics"
        return 0
    else
        print_error "Kafka is not accessible or has no topics"
        return 1
    fi
}

# Function to check Flink
check_flink() {
    local response=$(curl -s "http://localhost:8081/overview" 2>/dev/null || echo "failed")
    
    if [[ $response == *"slots"* ]]; then
        print_success "Flink is accessible and running"
        return 0
    else
        print_error "Flink is not accessible"
        return 1
    fi
}

# Function to test API functionality
test_api_functionality() {
    print_status "Testing API functionality..."
    
    # Test health endpoint
    if check_http_endpoint "Health endpoint" "$BASE_URL/health"; then
        local health_response=$(curl -s "$BASE_URL/health")
        if [[ $health_response == *"healthy"* ]]; then
            print_success "API health check passed"
        else
            print_warning "API health check returned unexpected response"
        fi
    fi
    
    # Test KPI endpoint
    if check_http_endpoint "KPI endpoint" "$BASE_URL/kpi"; then
        print_success "KPI endpoint is working"
    fi
    
    # Test alerts endpoint
    if check_http_endpoint "Alerts endpoint" "$BASE_URL/alerts"; then
        print_success "Alerts endpoint is working"
    fi
    
    # Test metrics endpoint
    if check_http_endpoint "Metrics endpoint" "$BASE_URL/metrics"; then
        print_success "Metrics endpoint is working"
    fi
}

# Function to check system resources
check_system_resources() {
    print_status "Checking system resources..."
    
    # Check Docker system
    local docker_info=$(docker system df --format "table {{.Type}}\t{{.TotalCount}}\t{{.Size}}")
    print_status "Docker system usage:"
    echo "$docker_info"
    
    # Check container resource usage
    print_status "Container resource usage:"
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"
}

# Function to check event flow
check_event_flow() {
    print_status "Checking event flow..."
    
    # Check if events are being produced
    local kafka_consumer_output=$(timeout 10s docker-compose -f $COMPOSE_FILE exec -T kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic events.v1 --from-beginning --max-messages 1 2>/dev/null || echo "no events")
    
    if [[ $kafka_consumer_output == *"event_id"* ]]; then
        print_success "Events are flowing through Kafka"
    else
        print_warning "No events detected in Kafka. You may need to start event generation."
    fi
    
    # Check if data is in database
    local db_count=$(docker-compose -f $COMPOSE_FILE exec -T timescaledb psql -U admin -d streaming_analytics -t -c "SELECT COUNT(*) FROM events_raw;" 2>/dev/null | tr -d ' \n' || echo "0")
    
    if [ "$db_count" -gt 0 ]; then
        print_success "Events are being stored in database ($db_count events)"
    else
        print_warning "No events found in database"
    fi
    
    # Check Redis for aggregates
    local redis_keys=$(docker-compose -f $COMPOSE_FILE exec -T redis redis-cli keys "agg:*" 2>/dev/null | wc -l || echo "0")
    
    if [ "$redis_keys" -gt 0 ]; then
        print_success "Aggregates are being stored in Redis ($redis_keys keys)"
    else
        print_warning "No aggregates found in Redis"
    fi
}

# Function to generate performance report
generate_performance_report() {
    print_status "Generating performance report..."
    
    local report_file="system-status-report-$(date +%Y%m%d_%H%M%S).txt"
    
    {
        echo "Real-Time Event Streaming Platform - System Status Report"
        echo "Generated: $(date)"
        echo "=============================================="
        echo ""
        
        echo "Container Status:"
        docker-compose -f $COMPOSE_FILE ps
        echo ""
        
        echo "Resource Usage:"
        docker stats --no-stream
        echo ""
        
        echo "Disk Usage:"
        docker system df
        echo ""
        
        echo "Network Information:"
        docker network ls
        echo ""
        
        echo "Volume Information:"
        docker volume ls
        echo ""
        
    } > "$report_file"
    
    print_success "Performance report saved to: $report_file"
}

# Function to run all checks
run_all_checks() {
    print_status "Real-Time Event Streaming Platform - System Status Check"
    echo "=================================================="
    echo ""
    
    local all_passed=true
    
    print_status "Checking infrastructure services..."
    check_service "zookeeper" || all_passed=false
    check_service "kafka" || all_passed=false
    check_service "redis" || all_passed=false
    check_service "timescaledb" || all_passed=false
    check_service "elasticsearch" || all_passed=false
    echo ""
    
    print_status "Checking processing services..."
    check_service "flink-jobmanager" || all_passed=false
    check_service "flink-taskmanager" || all_passed=false
    echo ""
    
    print_status "Checking application services..."
    check_service "read-api" || all_passed=false
    check_service "alert-service" || all_passed=false
    echo ""
    
    print_status "Checking monitoring services..."
    check_service "prometheus" || all_passed=false
    check_service "grafana" || all_passed=false
    check_service "alertmanager" || all_passed=false
    echo ""
    
    print_status "Checking UI services..."
    check_service "kafka-ui" || all_passed=false
    echo ""
    
    print_status "Testing connectivity..."
    check_database || all_passed=false
    check_redis || all_passed=false
    check_kafka || all_passed=false
    check_flink || all_passed=false
    echo ""
    
    print_status "Testing HTTP endpoints..."
    check_http_endpoint "Kafka UI" "http://localhost:8080"
    check_http_endpoint "Flink Dashboard" "http://localhost:8081"
    check_http_endpoint "Grafana" "http://localhost:3000"
    check_http_endpoint "Prometheus" "http://localhost:9090"
    echo ""
    
    test_api_functionality
    echo ""
    
    check_event_flow
    echo ""
    
    check_system_resources
    echo ""
    
    if [ "$all_passed" = true ]; then
        print_success "All critical services are running correctly! 🎉"
        echo ""
        print_status "Quick Start Guide:"
        echo "1. Access Grafana: http://localhost:3000 (admin/admin)"
        echo "2. View API docs: http://localhost:8000/docs"
        echo "3. Monitor Kafka: http://localhost:8080"
        echo "4. Check Flink jobs: http://localhost:8081"
        echo ""
        print_status "To start generating events:"
        echo "  cd ingestors/kafka-producer"
        echo "  python event_producer.py --rate 100"
        echo ""
        print_status "To run load tests:"
        echo "  ./scripts/run-load-tests.sh"
    else
        print_error "Some services are not running correctly. Please check the logs:"
        echo "  docker-compose -f $COMPOSE_FILE logs [service-name]"
    fi
    
    # Generate report
    if command -v docker >/dev/null 2>&1; then
        generate_performance_report
    fi
}

# Main execution
run_all_checks
