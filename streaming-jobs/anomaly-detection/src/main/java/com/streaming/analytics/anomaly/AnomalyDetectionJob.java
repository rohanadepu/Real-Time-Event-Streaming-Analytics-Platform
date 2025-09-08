package com.streaming.analytics.anomaly;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.FilterFunction;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.common.functions.RichFlatMapFunction;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.typeinfo.TypeHint;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.NoStoppingOffsetsInitializer;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import org.apache.flink.shaded.jackson2.com.fasterxml.jackson.databind.JsonNode;
import org.apache.flink.shaded.jackson2.com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.shaded.jackson2.com.fasterxml.jackson.databind.node.ObjectNode;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Queue;

/**
 * Flink job for real-time anomaly detection
 * 
 * This job:
 * 1. Consumes events from Kafka topic 'events.v1'
 * 2. Maintains rolling statistics per source using keyed state
 * 3. Detects anomalies using z-score analysis
 * 4. Sends alerts to Kafka topic 'alerts.v1'
 * 5. Uses configurable thresholds and rolling window sizes
 */
public class AnomalyDetectionJob {
    
    private static final String KAFKA_BOOTSTRAP_SERVERS = "kafka:29092";
    private static final String INPUT_TOPIC = "events.v1";
    private static final String OUTPUT_TOPIC = "alerts.v1";
    private static final String DB_SINK_TOPIC = "anomalies.db";
    
    // Anomaly detection parameters
    private static final int ROLLING_WINDOW_SIZE = 100; // Number of events to keep for rolling stats
    private static final double Z_SCORE_THRESHOLD = 3.0; // Z-score threshold for anomaly detection
    private static final double MAD_THRESHOLD = 3.0; // Median Absolute Deviation threshold
    private static final long MIN_EVENTS_FOR_DETECTION = 10; // Minimum events before starting detection
    
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
                .setGroupId("flink-anomaly-detection-job")
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
                .filter(event -> event != null && event.metric > 0); // Filter out invalid events
        
        // Detect anomalies using keyed state for rolling statistics
        DataStream<AnomalyAlert> anomalies = events
                .keyBy(event -> event.source)
                .flatMap(new AnomalyDetector());
        
        // Filter out non-anomalies
        DataStream<AnomalyAlert> confirmedAnomalies = anomalies
                .filter(alert -> alert != null && alert.isAnomaly);
        
        // Send alerts to Kafka topic
        DataStream<String> alertMessages = confirmedAnomalies
                .map(new AlertMessageFormatter());
        
        KafkaSink<String> alertSink = KafkaSink.<String>builder()
                .setBootstrapServers(KAFKA_BOOTSTRAP_SERVERS)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic(OUTPUT_TOPIC)
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .build();
        
        alertMessages.sinkTo(alertSink).name("alert-sink");
        
        // Send anomalies to database for storage
        DataStream<String> dbMessages = confirmedAnomalies
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
        env.execute("Anomaly Detection Job");
    }
    
    private static long extractEventTimestamp(String eventJson) {
        try {
            ObjectMapper mapper = new ObjectMapper();
            JsonNode node = mapper.readTree(eventJson);
            String timestamp = node.get("timestamp").asText();
            return Instant.parse(timestamp).toEpochMilli();
        } catch (Exception e) {
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
    }
    
    // Anomaly alert class
    public static class AnomalyAlert {
        public String alertId;
        public String source;
        public long timestamp;
        public String anomalyType;
        public String severity;
        public double value;
        public double threshold;
        public double zScore;
        public double madScore;
        public String description;
        public boolean isAnomaly;
        public RollingStats stats;
        
        public AnomalyAlert() {}
    }
    
    // Rolling statistics class
    public static class RollingStats {
        public Queue<Double> values;
        public double sum;
        public double sumSquares;
        public int maxSize;
        
        public RollingStats(int maxSize) {
            this.values = new ArrayDeque<>();
            this.sum = 0.0;
            this.sumSquares = 0.0;
            this.maxSize = maxSize;
        }
        
        public void addValue(double value) {
            if (values.size() >= maxSize) {
                double removed = values.poll();
                sum -= removed;
                sumSquares -= removed * removed;
            }
            
            values.offer(value);
            sum += value;
            sumSquares += value * value;
        }
        
        public double getMean() {
            return values.isEmpty() ? 0.0 : sum / values.size();
        }
        
        public double getStandardDeviation() {
            if (values.size() < 2) return 0.0;
            
            double mean = getMean();
            double variance = (sumSquares / values.size()) - (mean * mean);
            return Math.sqrt(Math.max(0, variance));
        }
        
        public double getMedian() {
            if (values.isEmpty()) return 0.0;
            
            Double[] sortedValues = values.toArray(new Double[0]);
            java.util.Arrays.sort(sortedValues);
            
            int size = sortedValues.length;
            if (size % 2 == 0) {
                return (sortedValues[size / 2 - 1] + sortedValues[size / 2]) / 2.0;
            } else {
                return sortedValues[size / 2];
            }
        }
        
        public double getMAD() {
            if (values.isEmpty()) return 0.0;
            
            double median = getMedian();
            Double[] deviations = new Double[values.size()];
            int i = 0;
            for (Double value : values) {
                deviations[i++] = Math.abs(value - median);
            }
            
            java.util.Arrays.sort(deviations);
            int size = deviations.length;
            if (size % 2 == 0) {
                return (deviations[size / 2 - 1] + deviations[size / 2]) / 2.0;
            } else {
                return deviations[size / 2];
            }
        }
        
        public int size() {
            return values.size();
        }
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
                System.err.println("Failed to parse event: " + value + ", error: " + e.getMessage());
                return null;
            }
        }
    }
    
    // Anomaly detector with keyed state
    public static class AnomalyDetector extends RichFlatMapFunction<EventData, AnomalyAlert> {
        
        private ValueState<RollingStats> statsState;
        private final ObjectMapper mapper = new ObjectMapper();
        
        @Override
        public void open(Configuration parameters) throws Exception {
            ValueStateDescriptor<RollingStats> descriptor = new ValueStateDescriptor<>(
                "rolling-stats",
                TypeInformation.of(new TypeHint<RollingStats>() {})
            );
            statsState = getRuntimeContext().getState(descriptor);
        }
        
        @Override
        public void flatMap(EventData event, Collector<AnomalyAlert> out) throws Exception {
            // Get or initialize rolling statistics for this source
            RollingStats stats = statsState.value();
            if (stats == null) {
                stats = new RollingStats(ROLLING_WINDOW_SIZE);
            }
            
            // Add current metric to rolling window
            stats.addValue(event.metric);
            
            // Update state
            statsState.update(stats);
            
            // Only perform anomaly detection after collecting minimum events
            if (stats.size() < MIN_EVENTS_FOR_DETECTION) {
                return; // Not enough data for reliable detection
            }
            
            // Calculate z-score
            double mean = stats.getMean();
            double stdDev = stats.getStandardDeviation();
            double zScore = stdDev > 0 ? (event.metric - mean) / stdDev : 0.0;
            
            // Calculate MAD-based score (more robust to outliers)
            double median = stats.getMedian();
            double mad = stats.getMAD();
            double madScore = mad > 0 ? Math.abs(event.metric - median) / mad : 0.0;
            
            // Determine if this is an anomaly
            boolean isZScoreAnomaly = Math.abs(zScore) > Z_SCORE_THRESHOLD;
            boolean isMadAnomaly = madScore > MAD_THRESHOLD;
            boolean isAnomaly = isZScoreAnomaly || isMadAnomaly;
            
            // Determine severity
            String severity = "info";
            if (isAnomaly) {
                if (Math.abs(zScore) > 4.0 || madScore > 4.0) {
                    severity = "critical";
                } else if (Math.abs(zScore) > 3.5 || madScore > 3.5) {
                    severity = "warning";
                } else {
                    severity = "info";
                }
            }
            
            // Create alert (even for non-anomalies for monitoring purposes)
            AnomalyAlert alert = new AnomalyAlert();
            alert.alertId = java.util.UUID.randomUUID().toString();
            alert.source = event.source;
            alert.timestamp = event.timestamp;
            alert.anomalyType = isZScoreAnomaly ? "z-score" : (isMadAnomaly ? "mad" : "normal");
            alert.severity = severity;
            alert.value = event.metric;
            alert.threshold = isZScoreAnomaly ? Z_SCORE_THRESHOLD : MAD_THRESHOLD;
            alert.zScore = zScore;
            alert.madScore = madScore;
            alert.isAnomaly = isAnomaly;
            alert.stats = stats;
            
            // Create description
            if (isAnomaly) {
                alert.description = String.format(
                    "Anomaly detected in %s: value=%.2f, mean=%.2f, z-score=%.2f, mad-score=%.2f",
                    event.source, event.metric, mean, zScore, madScore
                );
            } else {
                alert.description = String.format(
                    "Normal value in %s: value=%.2f, mean=%.2f, z-score=%.2f",
                    event.source, event.metric, mean, zScore
                );
            }
            
            out.collect(alert);
        }
    }
    
    // Alert message formatter for Kafka
    public static class AlertMessageFormatter implements MapFunction<AnomalyAlert, String> {
        private final ObjectMapper mapper = new ObjectMapper();
        
        @Override
        public String map(AnomalyAlert alert) throws Exception {
            ObjectNode message = mapper.createObjectNode();
            message.put("alert_id", alert.alertId);
            message.put("source", alert.source);
            message.put("timestamp", Instant.ofEpochMilli(alert.timestamp).toString());
            message.put("anomaly_type", alert.anomalyType);
            message.put("severity", alert.severity);
            message.put("value", alert.value);
            message.put("threshold", alert.threshold);
            message.put("z_score", alert.zScore);
            message.put("mad_score", alert.madScore);
            message.put("description", alert.description);
            message.put("is_anomaly", alert.isAnomaly);
            
            if (alert.stats != null) {
                ObjectNode stats = mapper.createObjectNode();
                stats.put("mean", alert.stats.getMean());
                stats.put("std_dev", alert.stats.getStandardDeviation());
                stats.put("median", alert.stats.getMedian());
                stats.put("mad", alert.stats.getMAD());
                stats.put("sample_size", alert.stats.size());
                message.set("stats", stats);
            }
            
            return mapper.writeValueAsString(message);
        }
    }
    
    // Database message formatter
    public static class DatabaseMessageFormatter implements MapFunction<AnomalyAlert, String> {
        private final ObjectMapper mapper = new ObjectMapper();
        
        @Override
        public String map(AnomalyAlert alert) throws Exception {
            ObjectNode message = mapper.createObjectNode();
            message.put("type", "anomaly_insert");
            
            ObjectNode data = mapper.createObjectNode();
            data.put("ts", Instant.ofEpochMilli(alert.timestamp).toString());
            data.put("source", alert.source);
            data.put("anomaly_type", alert.anomalyType);
            data.put("severity", alert.severity);
            data.put("value", alert.value);
            data.put("threshold", alert.threshold);
            data.put("z_score", alert.zScore);
            data.put("description", alert.description);
            data.put("resolved", false);
            
            message.set("data", data);
            
            return mapper.writeValueAsString(message);
        }
    }
}
