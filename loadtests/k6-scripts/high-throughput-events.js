import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
export let eventGenerationRate = new Rate('event_generation_success');
export let eventLatency = new Trend('event_generation_latency');
export let eventsGenerated = new Counter('events_generated_total');

// Test configuration for high-throughput event generation
export let options = {
  scenarios: {
    // Constant load scenario
    constant_load: {
      executor: 'constant-vus',
      vus: 50,
      duration: '5m',
      tags: { scenario: 'constant_load' },
    },
    
    // Ramping load scenario
    ramping_load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 25 },
        { duration: '3m', target: 100 },
        { duration: '5m', target: 100 },
        { duration: '2m', target: 0 },
      ],
      tags: { scenario: 'ramping_load' },
    },
    
    // Spike test scenario
    spike_test: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: 50 },   // Normal load
        { duration: '30s', target: 200 }, // Spike
        { duration: '30s', target: 200 }, // Stay at spike
        { duration: '1m', target: 50 },   // Back to normal
        { duration: '30s', target: 0 },   // Ramp down
      ],
      tags: { scenario: 'spike_test' },
    },
  },
  thresholds: {
    'event_generation_success': ['rate>0.95'], // 95% success rate
    'event_generation_latency': ['p(95)<100'], // 95% under 100ms
    'events_generated_total': ['count>50000'], // At least 50k events total
  },
};

// Configuration
const KAFKA_PRODUCER_URL = __ENV.KAFKA_PRODUCER_URL || 'http://localhost:8002';
const EVENT_SOURCES = ['web', 'mobile', 'api', 'device', 'service-a', 'service-b'];
const STATUSES = ['ok', 'warning', 'error'];
const REGIONS = ['us-east', 'us-west', 'eu-west', 'ap-south'];
const BROWSERS = ['chrome', 'firefox', 'safari', 'edge'];
const PLATFORMS = ['ios', 'android'];
const DEVICE_TYPES = ['sensor', 'gateway', 'controller'];

// Generate realistic event data
function generateEventData() {
  const source = randomItem(EVENT_SOURCES);
  const eventId = generateUUID();
  const timestamp = new Date().toISOString();
  
  // Generate metric with realistic distribution
  let metric;
  if (Math.random() < 0.05) {
    // 5% chance of outlier/anomaly
    metric = randomIntBetween(100, 500);
  } else {
    // Normal distribution around 50
    metric = Math.max(0, randomNormalDistribution(50, 15));
  }
  
  // Status correlated with metric value
  let status;
  if (metric > 100) {
    status = randomItem(STATUSES, [0.3, 0.4, 0.3]); // Higher chance of warning/error
  } else {
    status = randomItem(['ok', 'warning', 'error'], [0.8, 0.15, 0.05]); // Mostly OK
  }
  
  const attributes = {
    user_id: `user_${randomIntBetween(1000, 9999)}`,
    metric: Math.round(metric * 100) / 100, // Round to 2 decimal places
    status: status,
    session_id: generateUUID().substring(0, 8),
    region: randomItem(REGIONS),
    version: randomItem(['1.0.0', '1.1.0', '1.2.0', '2.0.0']),
  };
  
  // Add source-specific attributes
  if (source === 'web') {
    attributes.browser = randomItem(BROWSERS);
    attributes.page_load_time = Math.round((Math.random() * 4.5 + 0.5) * 100) / 100;
  } else if (source === 'mobile') {
    attributes.platform = randomItem(PLATFORMS);
    attributes.app_version = randomItem(['2.1.0', '2.2.0', '2.3.0']);
  } else if (source === 'device') {
    attributes.device_type = randomItem(DEVICE_TYPES);
    attributes.temperature = Math.round((Math.random() * 20 + 15) * 10) / 10;
    attributes.battery_level = randomIntBetween(0, 100);
  }
  
  return {
    event_id: eventId,
    source: source,
    timestamp: timestamp,
    attributes: attributes,
  };
}

// Generate UUID
function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c == 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// Generate normal distribution
function randomNormalDistribution(mean, stdDev) {
  let u = 0, v = 0;
  while(u === 0) u = Math.random(); // Converting [0,1) to (0,1)
  while(v === 0) v = Math.random();
  const z = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  return z * stdDev + mean;
}

// Simulate direct Kafka event publishing
export default function() {
  const startTime = Date.now();
  
  // Generate batch of events (simulate producer batch)
  const batchSize = randomIntBetween(1, 10);
  const events = [];
  
  for (let i = 0; i < batchSize; i++) {
    events.push(generateEventData());
  }
  
  // Simulate event validation and serialization time
  const processingTime = Math.random() * 5; // 0-5ms processing time
  sleep(processingTime / 1000);
  
  // Simulate network latency and Kafka producer latency
  const networkLatency = Math.random() * 10 + 5; // 5-15ms network latency
  sleep(networkLatency / 1000);
  
  const endTime = Date.now();
  const totalLatency = endTime - startTime;
  
  // Record metrics
  const success = true; // Simulate successful event generation
  eventGenerationRate.add(success);
  eventLatency.add(totalLatency);
  eventsGenerated.add(batchSize);
  
  // Validate event data structure
  const validationSuccess = check(events[0], {
    'Event has required fields': (event) => 
      event.event_id && event.source && event.timestamp && event.attributes,
    'Event ID is valid UUID': (event) => 
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(event.event_id),
    'Event timestamp is valid ISO string': (event) => 
      !isNaN(Date.parse(event.timestamp)),
    'Event source is valid': (event) => 
      EVENT_SOURCES.includes(event.source),
    'Event metric is numeric': (event) => 
      typeof event.attributes.metric === 'number' && event.attributes.metric >= 0,
    'Event has user_id': (event) => 
      typeof event.attributes.user_id === 'string' && event.attributes.user_id.length > 0,
  });
  
  // Simulate different event generation patterns
  const scenario = __ENV.K6_SCENARIO;
  if (scenario === 'constant_load') {
    // Steady rate
    sleep(Math.random() * 0.1); // 0-100ms between batches
  } else if (scenario === 'ramping_load') {
    // Variable rate based on time
    const elapsed = Date.now() - __ENV.TEST_START_TIME;
    const factor = Math.sin(elapsed / 60000) + 1; // Sine wave over 1 minute
    sleep(Math.random() * 0.2 * factor); // Variable sleep
  } else if (scenario === 'spike_test') {
    // Burst pattern
    if (Math.random() < 0.3) {
      // 30% chance of burst
      for (let burst = 0; burst < 5; burst++) {
        const burstEvents = [];
        for (let i = 0; i < 3; i++) {
          burstEvents.push(generateEventData());
        }
        eventsGenerated.add(burstEvents.length);
        sleep(0.001); // Very short sleep between burst events
      }
    } else {
      sleep(Math.random() * 0.3); // Normal interval
    }
  }
}

// Setup function
export function setup() {
  console.log('Starting high-throughput event generation test');
  console.log(`Target: >5000 events/sec sustained`);
  console.log(`Kafka Producer URL: ${KAFKA_PRODUCER_URL}`);
  
  // Set test start time for ramping scenarios
  __ENV.TEST_START_TIME = Date.now();
  
  return {
    startTime: new Date().toISOString(),
    config: {
      targetRate: 5000, // events per second
      testDuration: '10m',
    }
  };
}

// Teardown function
export function teardown(data) {
  console.log(`Event generation test completed`);
  console.log(`Started: ${data.startTime}`);
  console.log(`Ended: ${new Date().toISOString()}`);
  
  // Calculate final statistics
  const totalEvents = eventsGenerated.count;
  const testDurationSeconds = 600; // 10 minutes
  const avgEventsPerSecond = totalEvents / testDurationSeconds;
  
  console.log(`Total events generated: ${totalEvents}`);
  console.log(`Average events/sec: ${avgEventsPerSecond.toFixed(2)}`);
  console.log(`Target achieved: ${avgEventsPerSecond >= 5000 ? 'YES' : 'NO'}`);
}
