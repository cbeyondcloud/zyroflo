package com.example.orderflow.api;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

// Item 91: the wrapper idiom. Services do not call the client; they call this. The
// parameter is deliberately NOT named "topic" — a wrapper is recognised by its parameter
// FORWARDING into the client send, never by what anything is named.
@Component
public class EventGateway {

    private KafkaTemplate<String, Object> kafkaTemplate;

    public void push(String destination, Object payload) {
        kafkaTemplate.send(destination, payload);
    }
}
