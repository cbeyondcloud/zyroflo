package com.example.orderflow.api;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

// Item 91: the caller side. Four shapes: a wrapper call with a getter, a wrapper call
// with a literal, a direct client send with a convention-only getter, and a wrapper
// call passing this method's own parameter — which nothing can resolve and which must
// be recorded, not guessed (this extractor's one-level bound, stated as a fact).
@Service
public class AuditTrail {

    private EventGateway gateway;
    private NotifyConfig config;
    private KafkaTemplate<String, String> kafkaTemplate;

    public void onOrderEvent(Object evt) {
        gateway.push(config.getNotifyTopic(), evt);
        gateway.push("order-audit-raw", evt);
    }

    public void onAudit(Object evt, String routed) {
        kafkaTemplate.send(config.getStreamTopic(), evt);
        gateway.push(routed, evt);
    }
}
