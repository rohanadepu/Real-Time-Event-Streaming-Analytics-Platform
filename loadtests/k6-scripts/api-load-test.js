import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
export let errorRate = new Rate('errors');
export let latency = new Trend('latency');
export let requestCount = new Counter('requests');

// Test configuration
export let options = {
  stages: [
    // Ramp up
    { duration: '2m', target: 10 },   // Ramp up to 10 users
    { duration: '5m', target: 50 },   // Ramp up to 50 users
    { duration: '10m', target: 100 }, // Ramp up to 100 users
    
    // Steady state
    { duration: '10m', target: 100 }, // Stay at 100 users
    
    // Peak load
    { duration: '5m', target: 200 },  // Spike to 200 users
    { duration: '5m', target: 200 },  // Stay at peak
    
    // Ramp down
    { duration: '5m', target: 50 },   // Ramp down to 50
    { duration: '2m', target: 0 },    // Ramp down to 0
  ],
  thresholds: {
    http_req_duration: ['p(95)<150'], // 95% of requests must be below 150ms
    http_req_failed: ['rate<0.05'],   // Error rate must be below 5%
    'errors': ['rate<0.05'],
  },
};

// Base URLs
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_ENDPOINTS = {
  health: `${BASE_URL}/health`,
  kpi: `${BASE_URL}/kpi`,
  series: `${BASE_URL}/series`,
  alerts: `${BASE_URL}/alerts`,
};

// Test data
const SOURCES = ['web', 'mobile', 'api', 'device', 'service-a', 'service-b'];
const WINDOWS = ['1m', '5m', '15m', '1h'];
const AGGREGATIONS = ['avg', 'sum', 'count', 'p95'];

// Helper functions
function randomChoice(array) {
  return array[Math.floor(Math.random() * array.length)];
}

function getRandomTimestamp(hoursBack = 24) {
  const now = new Date();
  const past = new Date(now.getTime() - (hoursBack * 60 * 60 * 1000));
  const randomTime = new Date(past.getTime() + Math.random() * (now.getTime() - past.getTime()));
  return randomTime.toISOString();
}

// Main test function
export default function() {
  // Randomly choose which endpoint to test
  const testChoice = Math.random();
  
  if (testChoice < 0.4) {
    // 40% - Test KPI endpoint
    testKPIEndpoint();
  } else if (testChoice < 0.7) {
    // 30% - Test series endpoint
    testSeriesEndpoint();
  } else if (testChoice < 0.9) {
    // 20% - Test alerts endpoint
    testAlertsEndpoint();
  } else {
    // 10% - Test health endpoint
    testHealthEndpoint();
  }
  
  // Short sleep between requests
  sleep(Math.random() * 2 + 1); // 1-3 seconds
}

function testKPIEndpoint() {
  const source = Math.random() < 0.7 ? randomChoice(SOURCES) : null; // 70% chance to specify source
  const window = randomChoice(WINDOWS);
  
  const params = {};
  if (source) params.source = source;
  params.window = window;
  
  const url = `${API_ENDPOINTS.kpi}?${new URLSearchParams(params).toString()}`;
  
  const response = http.get(url, {
    headers: { 'Accept': 'application/json' },
    tags: { endpoint: 'kpi' },
  });
  
  const success = check(response, {
    'KPI status is 200': (r) => r.status === 200,
    'KPI response time < 150ms': (r) => r.timings.duration < 150,
    'KPI response is JSON': (r) => r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json'),
    'KPI response has data': (r) => {
      try {
        const data = JSON.parse(r.body);
        return Array.isArray(data);
      } catch (e) {
        return false;
      }
    },
  });
  
  errorRate.add(!success);
  latency.add(response.timings.duration);
  requestCount.add(1);
}

function testSeriesEndpoint() {
  const source = Math.random() < 0.8 ? randomChoice(SOURCES) : null; // 80% chance to specify source
  const aggregation = randomChoice(AGGREGATIONS);
  
  // Generate time range (last 1-24 hours)
  const hoursBack = Math.random() * 23 + 1;
  const endTime = new Date();
  const startTime = new Date(endTime.getTime() - (hoursBack * 60 * 60 * 1000));
  
  const params = {
    from: startTime.toISOString(),
    to: endTime.toISOString(),
    aggregation: aggregation,
  };
  if (source) params.source = source;
  
  const url = `${API_ENDPOINTS.series}?${new URLSearchParams(params).toString()}`;
  
  const response = http.get(url, {
    headers: { 'Accept': 'application/json' },
    tags: { endpoint: 'series' },
  });
  
  const success = check(response, {
    'Series status is 200': (r) => r.status === 200,
    'Series response time < 500ms': (r) => r.timings.duration < 500, // Allow more time for historical queries
    'Series response is JSON': (r) => r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json'),
    'Series response has data structure': (r) => {
      try {
        const data = JSON.parse(r.body);
        return Array.isArray(data) && (data.length === 0 || (data[0].source && Array.isArray(data[0].data)));
      } catch (e) {
        return false;
      }
    },
  });
  
  errorRate.add(!success);
  latency.add(response.timings.duration);
  requestCount.add(1);
}

function testAlertsEndpoint() {
  const params = {};
  
  // Sometimes add filters
  if (Math.random() < 0.5) {
    params.since = getRandomTimestamp(48); // Last 48 hours
  }
  if (Math.random() < 0.3) {
    params.resolved = Math.random() < 0.5 ? 'true' : 'false';
  }
  if (Math.random() < 0.2) {
    params.severity = randomChoice(['info', 'warning', 'critical']);
  }
  
  const url = Object.keys(params).length > 0 
    ? `${API_ENDPOINTS.alerts}?${new URLSearchParams(params).toString()}`
    : API_ENDPOINTS.alerts;
  
  const response = http.get(url, {
    headers: { 'Accept': 'application/json' },
    tags: { endpoint: 'alerts' },
  });
  
  const success = check(response, {
    'Alerts status is 200': (r) => r.status === 200,
    'Alerts response time < 200ms': (r) => r.timings.duration < 200,
    'Alerts response is JSON': (r) => r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json'),
    'Alerts response has data structure': (r) => {
      try {
        const data = JSON.parse(r.body);
        return Array.isArray(data);
      } catch (e) {
        return false;
      }
    },
  });
  
  errorRate.add(!success);
  latency.add(response.timings.duration);
  requestCount.add(1);
}

function testHealthEndpoint() {
  const response = http.get(API_ENDPOINTS.health, {
    headers: { 'Accept': 'application/json' },
    tags: { endpoint: 'health' },
  });
  
  const success = check(response, {
    'Health status is 200': (r) => r.status === 200,
    'Health response time < 50ms': (r) => r.timings.duration < 50,
    'Health response is JSON': (r) => r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json'),
    'Health response has status': (r) => {
      try {
        const data = JSON.parse(r.body);
        return data.status && data.services;
      } catch (e) {
        return false;
      }
    },
  });
  
  errorRate.add(!success);
  latency.add(response.timings.duration);
  requestCount.add(1);
}

// Setup function
export function setup() {
  console.log('Starting load test for Real-Time Event Streaming API');
  console.log(`Base URL: ${BASE_URL}`);
  
  // Test connectivity
  const healthResponse = http.get(API_ENDPOINTS.health);
  if (healthResponse.status !== 200) {
    throw new Error(`Health check failed: ${healthResponse.status}`);
  }
  
  console.log('Health check passed, starting load test...');
  return { timestamp: new Date().toISOString() };
}

// Teardown function
export function teardown(data) {
  console.log(`Load test completed at ${new Date().toISOString()}`);
  console.log(`Test started at: ${data.timestamp}`);
}
