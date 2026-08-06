package com.example.orderflow.worker;

import org.springframework.kafka.annotation.KafkaListener;

/** Hop 4: the consuming half of the chain. */
public class OrderConsumer {

    @KafkaListener(topics = "${orders.consume.topic}", groupId = "${orders.consume.group}")
    public void onMessage(String payload) {
        // persisted via the write mapping, not by this class
    }
}
