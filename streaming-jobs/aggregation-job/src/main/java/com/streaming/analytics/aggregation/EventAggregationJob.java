package com.streaming.analytics.aggregation;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.AggregateFunction;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.NoStoppingOffsetsInitializer;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.SlidingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.apache.flink.shaded.jackson2.com.fasterxml.jackson.databind.JsonNode;
import org.apache.flink.shaded.jackson2.com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.shaded.jackson2.com.fasterxml.jackson.databind.node.ObjectNode;

import java.time.Duration;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Properties;

/**
 * Flink job for real-time event aggregation
 * 
 * This job:
 * 1. Consumes events from Kafka topic 'events.v1'
 * 2. Performs sliding window aggregations (1min windows, sliding every 10s)
 * 3. Computes count, avg, p95, p99 metrics per source
 * 4. Sends aggregates to Redis via a sink topic
 * 5. Stores aggregates in TimescaleDB via another sink topic
 */
public class EventAggregationJob {
    
    private static final String KAFKA_BOOTSTRAP_SERVERS = "kafka:29092";
    private static final String INPUT_TOPIC = "events.v1";
    private static final String REDIS_SINK_TOPIC = "aggregates.redis";
    private static final String DB_SINK_TOPIC = "aggregates.db";
    
    public static void main(String[] args) throws Exception {
        // Set up the execution environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        
        // Configure checkpointing for fault tolerance
        env.enableCheckpointing(30000); // 30 seconds
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(5000);
        env.getCheckpointConfig().setCheckpointTimeout(60000);
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);
        
        // Configure parallelism
        env.setParallelism(2);
        
        // Create Kafka source
        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(KAFKA_BOOTSTRAP_SERVERS)
                .setTopics(INPUT_TOPIC)
                .setGroupId("flink-aggregation-job")
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .build();
        
        // Create data stream from Kafka
        DataStream<String> rawEvents = env.fromSource(source, 
                WatermarkStrategy.<String>forBoundedOutOfOrderness(Duration.ofSeconds(10))
                        .withTimestampAssigner((event, timestamp) -> extractEventTimestamp(event)),
                "kafka-source");
        
        // Parse and validate events
        DataStream<EventData> events = rawEvents
                .map(new EventParser())
                .filter(event -> event != null);
        
        // Perform sliding window aggregations
        DataStream<AggregateResult> aggregates = events
                .keyBy(event -> event.source)
                .window(SlidingEventTimeWindows.of(Time.minutes(1), Time.seconds(10)))
                .aggregate(new EventAggregator());
        
        // Send aggregates to Redis (for hot queries)
        DataStream<String> redisMessages = aggregates
                .map(new RedisMessageFormatter());
        
        KafkaSink<String> redisSink = KafkaSink.<String>builder()
                .setBootstrapServers(KAFKA_BOOTSTRAP_SERVERS)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic(REDIS_SINK_TOPIC)
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .build();
        
        redisMessages.sinkTo(redisSink).name("redis-sink");
        
        // Send aggregates to database (for cold storage)
        DataStream<String> dbMessages = aggregates
                .map(new DatabaseMessageFormatter());
        
        KafkaSink<String> dbSink = KafkaSink.<String>builder()
                .setBootstrapServers(KAFKA_BOOTSTRAP_SERVERS)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic(DB_SINK_TOPIC)
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .build();
        
        dbMessages.sinkTo(dbSink).name("database-sink");
        
        // Execute the job
        env.execute("Event Aggregation Job");
    }
    
    private static long extractEventTimestamp(String eventJson) {
        try {
            ObjectMapper mapper = new ObjectMapper();
            JsonNode node = mapper.readTree(eventJson);
            String timestamp = node.get("timestamp").asText();
            return Instant.parse(timestamp).toEpochMilli();
        } catch (Exception e) {
            // Fallback to current time
            return System.currentTimeMillis();
        }
    }
    
    // Event data class
    public static class EventData {
        public String eventId;
        public String source;
        public long timestamp;
        public double metric;
        public String status;
        public String userId;
        
        public EventData() {}
        
        public EventData(String eventId, String source, long timestamp, 
                        double metric, String status, String userId) {
            this.eventId = eventId;
            this.source = source;
            this.timestamp = timestamp;
            this.metric = metric;
            this.status = status;
            this.userId = userId;
        }
        
        public boolean isError() {
            return "error".equals(status);
        }
    }
    
    // Aggregate result class
    public static class AggregateResult {
        public String source;
        public long windowStart;
        public long windowEnd;
        public long count;
        public double sum;
        public double avg;
        public double p95;
        public double p99;
        public long errorCount;
        public double errorRate;
        
        public AggregateResult() {}
    }
    
    // Event parser
    public static class EventParser implements MapFunction<String, EventData> {
        private final ObjectMapper mapper = new ObjectMapper();
        
        @Override
        public EventData map(String value) throws Exception {
            try {
                JsonNode node = mapper.readTree(value);
                JsonNode attributes = node.get("attributes");
                
                return new EventData(
                    node.get("event_id").asText(),
                    node.get("source").asText(),
                    Instant.parse(node.get("timestamp").asText()).toEpochMilli(),
                    attributes.get("metric").asDouble(),
                    attributes.get("status").asText(),
                    attributes.get("user_id").asText()
                );
            } catch (Exception e) {
                // Log error and return null (will be filtered out)
                System.err.println("Failed to parse event: " + value + ", error: " + e.getMessage());
                return null;
            }
        }
    }
    
    // Event aggregator
    public static class EventAggregator implements AggregateFunction<EventData, EventAggregator.Accumulator, AggregateResult> {
        
        public static class Accumulator {
            public String source;
            public long windowStart;
            public long windowEnd;
            public long count;
            public double sum;
            public List<Double> metrics;
            public long errorCount;
            
            public Accumulator() {
                this.metrics = new ArrayList<>();
            }
        }
        
        @Override
        public Accumulator createAccumulator() {
            return new Accumulator();
        }
        
        @Override
        public Accumulator add(EventData event, Accumulator accumulator) {
            if (accumulator.source == null) {
                accumulator.source = event.source;
            }
            
            accumulator.count++;
            accumulator.sum += event.metric;
            accumulator.metrics.add(event.metric);
            
            if (event.isError()) {
                accumulator.errorCount++;
            }
            
            return accumulator;
        }
        
        @Override
        public AggregateResult getResult(Accumulator accumulator) {
            AggregateResult result = new AggregateResult();
            result.source = accumulator.source;
            result.windowStart = accumulator.windowStart;
            result.windowEnd = accumulator.windowEnd;
            result.count = accumulator.count;
            result.sum = accumulator.sum;
            result.avg = accumulator.count > 0 ? accumulator.sum / accumulator.count : 0.0;
            result.errorCount = accumulator.errorCount;
            result.errorRate = accumulator.count > 0 ? (double) accumulator.errorCount / accumulator.count : 0.0;
            
            // Calculate percentiles
            if (!accumulator.metrics.isEmpty()) {
                Collections.sort(accumulator.metrics);
                int size = accumulator.metrics.size();
                result.p95 = accumulator.metrics.get((int) (size * 0.95));
                result.p99 = accumulator.metrics.get((int) (size * 0.99));
            }
            
            return result;
        }
        
        @Override
        public Accumulator merge(Accumulator a, Accumulator b) {
            a.count += b.count;
            a.sum += b.sum;
            a.metrics.addAll(b.metrics);
            a.errorCount += b.errorCount;
            return a;
        }
    }
    
    // Redis message formatter
    public static class RedisMessageFormatter implements MapFunction<AggregateResult, String> {
        private final ObjectMapper mapper = new ObjectMapper();
        
        @Override
        public String map(AggregateResult aggregate) throws Exception {
            ObjectNode message = mapper.createObjectNode();
            message.put("type", "redis_aggregate");
            
            // Redis key format: agg:{source}:{window}:{timestamp}
            String redisKey = String.format("agg:%s:1m:%s", 
                aggregate.source, 
                Instant.ofEpochMilli(aggregate.windowStart).toString());
            
            ObjectNode payload = mapper.createObjectNode();
            payload.put("count", aggregate.count);
            payload.put("avg_metric", aggregate.avg);
            payload.put("p95_metric", aggregate.p95);
            payload.put("p99_metric", aggregate.p99);
            payload.put("error_rate", aggregate.errorRate);
            payload.put("sum_metric", aggregate.sum);
            
            message.put("key", redisKey);
            message.set("value", payload);
            message.put("ttl", 3600); // 1 hour TTL
            
            return mapper.writeValueAsString(message);
        }
    }
    
    // Database message formatter
    public static class DatabaseMessageFormatter implements MapFunction<AggregateResult, String> {
        private final ObjectMapper mapper = new ObjectMapper();
        
        @Override
        public String map(AggregateResult aggregate) throws Exception {
            ObjectNode message = mapper.createObjectNode();
            message.put("type", "db_aggregate");
            
            ObjectNode payload = mapper.createObjectNode();
            payload.put("ts", Instant.ofEpochMilli(aggregate.windowStart).toString());
            payload.put("source", aggregate.source);
            payload.put("count_events", aggregate.count);
            payload.put("avg_metric", aggregate.avg);
            payload.put("p95_metric", aggregate.p95);
            payload.put("p99_metric", aggregate.p99);
            payload.put("error_rate", aggregate.errorRate);
            payload.put("sum_metric", aggregate.sum);
            
            message.set("data", payload);
            
            return mapper.writeValueAsString(message);
        }
    }
}
