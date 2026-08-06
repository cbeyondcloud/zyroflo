package com.example.orderflow.api;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;

/** Hop 3: publishes to the topic named by a property, never a literal. */
public class OrderPublisher {

    @Value("${orders.publish.topic}")
    private String topic;

    private final KafkaTemplate<String, String> template;

    public OrderPublisher(KafkaTemplate<String, String> template) {
        this.template = template;
    }

    public void publish(String payload) {
        template.send(topic, payload);
    }
}
