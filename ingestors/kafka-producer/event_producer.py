import asyncio
import json
import uuid
import random
from datetime import datetime, timezone
from typing import Dict, Any, List
import logging
from dataclasses import dataclass, asdict
from kafka import KafkaProducer
from kafka.errors import KafkaError
import time
import argparse
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Prometheus metrics
EVENTS_PRODUCED = Counter('events_produced_total', 'Total number of events produced', ['source', 'status'])
PRODUCE_DURATION = Histogram('event_produce_duration_seconds', 'Time spent producing events')
KAFKA_ERRORS = Counter('kafka_errors_total', 'Total number of Kafka errors', ['error_type'])
EVENTS_PER_SECOND = Gauge('events_per_second', 'Current events per second rate')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Event:
    """Event data structure"""
    event_id: str
    source: str
    timestamp: str
    attributes: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

class EventGenerator:
    """Generates synthetic events for testing"""
    
    SOURCES = ['web', 'mobile', 'api', 'device', 'service-a', 'service-b']
    STATUSES = ['ok', 'warning', 'error']
    
    def __init__(self):
        self.user_ids = [f"user_{i}" for i in range(1000, 10000)]
    
    def generate_event(self, source: str = None) -> Event:
        """Generate a single synthetic event"""
        if source is None:
            source = random.choice(self.SOURCES)
        
        # Generate realistic metric values with some outliers
        if random.random() < 0.05:  # 5% chance of outlier
            metric = random.uniform(100, 500)  # Outlier values
        else:
            metric = random.normalvariate(50, 15)  # Normal distribution
            metric = max(0, metric)  # Ensure positive values
        
        # Generate status with some correlation to metric value
        if metric > 100:
            status = random.choices(
                self.STATUSES, 
                weights=[0.3, 0.4, 0.3]  # Higher chance of warning/error for high metrics
            )[0]
        else:
            status = random.choices(
                self.STATUSES, 
                weights=[0.8, 0.15, 0.05]  # Mostly OK for normal metrics
            )[0]
        
        attributes = {
            'user_id': random.choice(self.user_ids),
            'metric': round(metric, 2),
            'status': status,
            'session_id': str(uuid.uuid4())[:8],
            'region': random.choice(['us-east', 'us-west', 'eu-west', 'ap-south']),
            'version': random.choice(['1.0.0', '1.1.0', '1.2.0', '2.0.0'])
        }
        
        # Add some source-specific attributes
        if source == 'web':
            attributes.update({
                'browser': random.choice(['chrome', 'firefox', 'safari', 'edge']),
                'page_load_time': round(random.uniform(0.5, 5.0), 2)
            })
        elif source == 'mobile':
            attributes.update({
                'platform': random.choice(['ios', 'android']),
                'app_version': random.choice(['2.1.0', '2.2.0', '2.3.0'])
            })
        elif source.startswith('device'):
            attributes.update({
                'device_type': random.choice(['sensor', 'gateway', 'controller']),
                'temperature': round(random.uniform(15, 35), 1),
                'battery_level': random.randint(0, 100)
            })
        
        return Event(
            event_id=str(uuid.uuid4()),
            source=source,
            timestamp=datetime.now(timezone.utc).isoformat(),
            attributes=attributes
        )

class KafkaEventProducer:
    """Kafka event producer with monitoring and error handling"""
    
    def __init__(self, 
                 bootstrap_servers: List[str] = ['localhost:9092'],
                 topic: str = 'events.v1',
                 **kafka_config):
        
        self.topic = topic
        self.event_generator = EventGenerator()
        
        # Kafka producer configuration optimized for throughput
        producer_config = {
            'bootstrap_servers': bootstrap_servers,
            'value_serializer': lambda v: v.encode('utf-8'),
            'key_serializer': lambda k: k.encode('utf-8') if k else None,
            'acks': 1,  # Leader acknowledgment only for better throughput
            'retries': 3,
            'batch_size': 16384,
            'linger_ms': 10,  # Wait up to 10ms to batch requests
            'buffer_memory': 33554432,
            'compression_type': 'snappy',
            **kafka_config
        }
        
        self.producer = KafkaProducer(**producer_config)
        logger.info(f"Kafka producer initialized with config: {producer_config}")
    
    def delivery_callback(self, source: str):
        """Callback for successful/failed delivery"""
        def callback(record_metadata, exception):
            if exception:
                logger.error(f"Failed to deliver event: {exception}")
                KAFKA_ERRORS.labels(error_type=type(exception).__name__).inc()
                EVENTS_PRODUCED.labels(source=source, status='failed').inc()
            else:
                EVENTS_PRODUCED.labels(source=source, status='success').inc()
                logger.debug(f"Event delivered to {record_metadata.topic}[{record_metadata.partition}]:{record_metadata.offset}")
        
        return callback
    
    @PRODUCE_DURATION.time()
    def produce_event(self, event: Event):
        """Produce a single event to Kafka"""
        try:
            # Use source as key for partitioning
            future = self.producer.send(
                self.topic,
                key=event.source,
                value=event.to_json()
            )
            
            # Add callback for monitoring
            future.add_callback(self.delivery_callback(event.source))
            future.add_errback(lambda e: logger.error(f"Send failed: {e}"))
            
            return future
            
        except Exception as e:
            logger.error(f"Error producing event: {e}")
            KAFKA_ERRORS.labels(error_type=type(e).__name__).inc()
            raise
    
    def produce_batch(self, events: List[Event]):
        """Produce a batch of events"""
        futures = []
        for event in events:
            future = self.produce_event(event)
            futures.append(future)
        
        # Flush to ensure all events are sent
        self.producer.flush()
        return futures
    
    async def run_continuous(self, 
                           events_per_second: int = 100,
                           duration_seconds: int = None,
                           sources: List[str] = None):
        """Run continuous event generation"""
        logger.info(f"Starting continuous event generation at {events_per_second} events/sec")
        
        if sources is None:
            sources = EventGenerator.SOURCES
        
        start_time = time.time()
        event_count = 0
        last_rate_update = start_time
        
        try:
            while True:
                batch_start = time.time()
                
                # Generate events for this batch
                batch_size = min(events_per_second, 100)  # Limit batch size
                events = []
                
                for _ in range(batch_size):
                    source = random.choice(sources)
                    event = self.event_generator.generate_event(source)
                    events.append(event)
                
                # Produce the batch
                self.produce_batch(events)
                event_count += len(events)
                
                # Update rate metric every second
                current_time = time.time()
                if current_time - last_rate_update >= 1.0:
                    rate = event_count / (current_time - start_time)
                    EVENTS_PER_SECOND.set(rate)
                    last_rate_update = current_time
                    logger.info(f"Produced {event_count} events, rate: {rate:.2f} events/sec")
                
                # Check duration limit
                if duration_seconds and (current_time - start_time) >= duration_seconds:
                    logger.info(f"Reached duration limit of {duration_seconds} seconds")
                    break
                
                # Sleep to maintain rate
                batch_duration = time.time() - batch_start
                target_duration = len(events) / events_per_second
                sleep_time = max(0, target_duration - batch_duration)
                
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, stopping...")
        except Exception as e:
            logger.error(f"Error in continuous generation: {e}")
            raise
        finally:
            self.close()
    
    def close(self):
        """Close the producer and clean up resources"""
        if self.producer:
            self.producer.flush()
            self.producer.close()
            logger.info("Kafka producer closed")

def main():
    parser = argparse.ArgumentParser(description='Kafka Event Producer')
    parser.add_argument('--bootstrap-servers', default='localhost:9092', help='Kafka bootstrap servers')
    parser.add_argument('--topic', default='events.v1', help='Kafka topic')
    parser.add_argument('--rate', type=int, default=100, help='Events per second')
    parser.add_argument('--duration', type=int, help='Duration in seconds (infinite if not set)')
    parser.add_argument('--sources', nargs='+', help='List of sources to use')
    parser.add_argument('--metrics-port', type=int, default=8002, help='Prometheus metrics port')
    
    args = parser.parse_args()
    
    # Start Prometheus metrics server
    start_http_server(args.metrics_port)
    logger.info(f"Prometheus metrics available at http://localhost:{args.metrics_port}/metrics")
    
    # Create and run producer
    producer = KafkaEventProducer(
        bootstrap_servers=args.bootstrap_servers.split(','),
        topic=args.topic
    )
    
    # Run the producer
    asyncio.run(producer.run_continuous(
        events_per_second=args.rate,
        duration_seconds=args.duration,
        sources=args.sources
    ))

if __name__ == '__main__':
    main()
