package com.example.orderflow.api;

import lombok.Getter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

// Item 91: the config-class idiom. Topic names live here as @Value bindings and reach
// the producer through getters — one written out, one existing only through Lombok's
// class-level @Getter, so the extractor must map getStreamTopic -> streamTopic by the
// convention rather than by finding a method.
@Getter
@Component
public class NotifyConfig {

    @Value("${orders.notify.topic}")
    private String notifyTopic;

    @Value("${orders.stream.topic}")
    private String streamTopic;

    public String getNotifyTopic() {
        return notifyTopic;
    }
}
