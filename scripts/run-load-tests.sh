#!/bin/bash

# Load Testing Script for Real-Time Event Streaming Platform
# This script runs comprehensive load tests using k6

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
BASE_URL=${BASE_URL:-"http://localhost:8000"}
KAFKA_PRODUCER_URL=${KAFKA_PRODUCER_URL:-"http://localhost:8002"}
OUTPUT_DIR="loadtest-results"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

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

# Function to check if k6 is installed
check_k6() {
    if ! command -v k6 >/dev/null 2>&1; then
        print_error "k6 is not installed. Please install k6 first."
        print_status "Install k6:"
        print_status "  macOS: brew install k6"
        print_status "  Ubuntu: sudo apt install k6"
        print_status "  Windows: choco install k6"
        exit 1
    fi
    
    print_success "k6 is installed!"
}

# Function to check service health
check_services() {
    print_status "Checking service health..."
    
    # Check Read API
    if curl -s "$BASE_URL/health" >/dev/null; then
        print_success "Read API is healthy"
    else
        print_error "Read API is not accessible at $BASE_URL"
        exit 1
    fi
    
    # Check if event producer is generating events
    print_status "Checking if events are being generated..."
    sleep 5  # Wait a bit for events to be generated
    
    # Check if we can get some KPI data
    if curl -s "$BASE_URL/kpi" | grep -q '\[\]' || curl -s "$BASE_URL/kpi" | grep -q 'source'; then
        print_success "Event pipeline is working!"
    else
        print_warning "No events detected. You may want to start event generation first."
    fi
}

# Function to create output directory
setup_output_dir() {
    mkdir -p "$OUTPUT_DIR"
    print_status "Results will be saved to: $OUTPUT_DIR"
}

# Function to run API load test
run_api_load_test() {
    print_status "Running API load test..."
    
    local test_name="api-load-test"
    local output_file="$OUTPUT_DIR/${test_name}_${TIMESTAMP}"
    
    k6 run \
        --env BASE_URL="$BASE_URL" \
        --out json="$output_file.json" \
        --out csv="$output_file.csv" \
        loadtests/k6-scripts/api-load-test.js \
        | tee "$output_file.log"
    
    print_success "API load test completed!"
    print_status "Results saved to: $output_file.*"
}

# Function to run high throughput event test
run_event_throughput_test() {
    print_status "Running high throughput event simulation..."
    
    local test_name="high-throughput-events"
    local output_file="$OUTPUT_DIR/${test_name}_${TIMESTAMP}"
    
    k6 run \
        --env KAFKA_PRODUCER_URL="$KAFKA_PRODUCER_URL" \
        --out json="$output_file.json" \
        --out csv="$output_file.csv" \
        loadtests/k6-scripts/high-throughput-events.js \
        | tee "$output_file.log"
    
    print_success "High throughput event test completed!"
    print_status "Results saved to: $output_file.*"
}

# Function to run spike test
run_spike_test() {
    print_status "Running spike test..."
    
    local test_name="spike-test"
    local output_file="$OUTPUT_DIR/${test_name}_${TIMESTAMP}"
    
    # Create spike test configuration
    cat > "/tmp/spike-test.js" << 'EOF'
import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  stages: [
    { duration: '1m', target: 10 },   // Normal load
    { duration: '30s', target: 100 }, // Spike
    { duration: '1m', target: 100 },  // Stay at spike
    { duration: '30s', target: 10 },  // Recovery
    { duration: '1m', target: 10 },   // Normal load
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% under 500ms during spike
    http_req_failed: ['rate<0.1'],    // Less than 10% errors
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function() {
  const responses = http.batch([
    ['GET', `${BASE_URL}/health`],
    ['GET', `${BASE_URL}/kpi?window=1m`],
    ['GET', `${BASE_URL}/alerts`],
  ]);
  
  check(responses[0], {
    'health status is 200': (r) => r.status === 200,
  });
  
  check(responses[1], {
    'kpi status is 200': (r) => r.status === 200,
  });
  
  check(responses[2], {
    'alerts status is 200': (r) => r.status === 200,
  });
  
  sleep(Math.random() * 2 + 1);
}
EOF

    k6 run \
        --env BASE_URL="$BASE_URL" \
        --out json="$output_file.json" \
        --out csv="$output_file.csv" \
        /tmp/spike-test.js \
        | tee "$output_file.log"
    
    rm /tmp/spike-test.js
    
    print_success "Spike test completed!"
    print_status "Results saved to: $output_file.*"
}

# Function to run soak test
run_soak_test() {
    print_status "Running soak test (30 minutes)..."
    print_warning "This test will take 30 minutes. Press Ctrl+C to cancel."
    
    sleep 5  # Give time to cancel
    
    local test_name="soak-test"
    local output_file="$OUTPUT_DIR/${test_name}_${TIMESTAMP}"
    
    # Create soak test configuration
    cat > "/tmp/soak-test.js" << 'EOF'
import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  stages: [
    { duration: '5m', target: 20 },   // Ramp up
    { duration: '20m', target: 20 },  // Stay at load
    { duration: '5m', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<200'], // 95% under 200ms
    http_req_failed: ['rate<0.02'],   // Less than 2% errors
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function() {
  const endpoint = Math.random() < 0.5 ? '/kpi?window=1m' : '/alerts';
  
  const response = http.get(`${BASE_URL}${endpoint}`);
  
  check(response, {
    'status is 200': (r) => r.status === 200,
    'response time < 200ms': (r) => r.timings.duration < 200,
  });
  
  sleep(2);
}
EOF

    k6 run \
        --env BASE_URL="$BASE_URL" \
        --out json="$output_file.json" \
        --out csv="$output_file.csv" \
        /tmp/soak-test.js \
        | tee "$output_file.log"
    
    rm /tmp/soak-test.js
    
    print_success "Soak test completed!"
    print_status "Results saved to: $output_file.*"
}

# Function to generate test report
generate_report() {
    print_status "Generating test report..."
    
    local report_file="$OUTPUT_DIR/test_report_${TIMESTAMP}.html"
    
    cat > "$report_file" << EOF
<!DOCTYPE html>
<html>
<head>
    <title>Load Test Report - $(date)</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .header { background-color: #f0f0f0; padding: 20px; border-radius: 5px; }
        .test-section { margin: 20px 0; border: 1px solid #ddd; padding: 15px; border-radius: 5px; }
        .success { color: green; }
        .warning { color: orange; }
        .error { color: red; }
        table { border-collapse: collapse; width: 100%; margin: 10px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Real-Time Event Streaming Platform - Load Test Report</h1>
        <p><strong>Generated:</strong> $(date)</p>
        <p><strong>Target URL:</strong> $BASE_URL</p>
    </div>
    
    <div class="test-section">
        <h2>Test Summary</h2>
        <table>
            <tr>
                <th>Test Type</th>
                <th>Status</th>
                <th>Results File</th>
            </tr>
EOF

    # Add test results to report
    for log_file in "$OUTPUT_DIR"/*_"$TIMESTAMP".log; do
        if [ -f "$log_file" ]; then
            test_name=$(basename "$log_file" .log | sed "s/_${TIMESTAMP}//")
            
            if grep -q "✓" "$log_file"; then
                status="<span class='success'>PASSED</span>"
            elif grep -q "✗" "$log_file"; then
                status="<span class='error'>FAILED</span>"
            else
                status="<span class='warning'>UNKNOWN</span>"
            fi
            
            echo "            <tr><td>$test_name</td><td>$status</td><td>$(basename "$log_file")</td></tr>" >> "$report_file"
        fi
    done
    
    cat >> "$report_file" << EOF
        </table>
    </div>
    
    <div class="test-section">
        <h2>Key Metrics</h2>
        <p>Detailed metrics are available in the JSON and CSV files for each test.</p>
        
        <h3>Success Criteria</h3>
        <ul>
            <li>P95 response time &lt; 150ms for normal operations</li>
            <li>Error rate &lt; 5%</li>
            <li>System handles sustained load without degradation</li>
            <li>Spike recovery within 2 minutes</li>
        </ul>
    </div>
    
    <div class="test-section">
        <h2>Files Generated</h2>
        <ul>
EOF

    for file in "$OUTPUT_DIR"/*_"$TIMESTAMP".*; do
        if [ -f "$file" ]; then
            echo "            <li>$(basename "$file")</li>" >> "$report_file"
        fi
    done
    
    cat >> "$report_file" << EOF
        </ul>
    </div>
    
    <div class="test-section">
        <h2>Next Steps</h2>
        <ol>
            <li>Review detailed metrics in CSV/JSON files</li>
            <li>Analyze performance bottlenecks</li>
            <li>Optimize based on results</li>
            <li>Re-run tests to validate improvements</li>
        </ol>
    </div>
</body>
</html>
EOF

    print_success "Test report generated: $report_file"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS] [TEST_TYPE]"
    echo ""
    echo "Options:"
    echo "  -u, --url URL          Base URL for API tests (default: http://localhost:8000)"
    echo "  -p, --producer URL     Kafka producer URL (default: http://localhost:8002)"
    echo "  -h, --help            Show this help message"
    echo ""
    echo "Test Types:"
    echo "  api                   Run API load test only"
    echo "  events                Run event throughput test only"
    echo "  spike                 Run spike test only"
    echo "  soak                  Run soak test only"
    echo "  all                   Run all tests (default)"
    echo ""
    echo "Examples:"
    echo "  $0                    # Run all tests with default settings"
    echo "  $0 api                # Run only API load test"
    echo "  $0 -u http://staging.example.com all"
    echo ""
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--url)
            BASE_URL="$2"
            shift 2
            ;;
        -p|--producer)
            KAFKA_PRODUCER_URL="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            TEST_TYPE="$1"
            shift
            ;;
    esac
done

# Default test type
TEST_TYPE=${TEST_TYPE:-"all"}

# Main function
main() {
    print_status "Starting load testing for Real-Time Event Streaming Platform"
    print_status "Target URL: $BASE_URL"
    print_status "Test Type: $TEST_TYPE"
    echo ""
    
    check_k6
    check_services
    setup_output_dir
    
    case $TEST_TYPE in
        "api")
            run_api_load_test
            ;;
        "events")
            run_event_throughput_test
            ;;
        "spike")
            run_spike_test
            ;;
        "soak")
            run_soak_test
            ;;
        "all")
            run_api_load_test
            echo ""
            run_event_throughput_test
            echo ""
            run_spike_test
            echo ""
            print_status "Do you want to run the 30-minute soak test? (y/N)"
            read -r response
            if [[ $response =~ ^[Yy]$ ]]; then
                run_soak_test
            else
                print_status "Skipping soak test"
            fi
            ;;
        *)
            print_error "Unknown test type: $TEST_TYPE"
            show_usage
            exit 1
            ;;
    esac
    
    echo ""
    generate_report
    
    print_success "Load testing completed! 🎯"
    print_status "Check the results in: $OUTPUT_DIR"
}

# Run main function
main "$@"
