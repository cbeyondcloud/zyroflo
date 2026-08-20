package com.example.orderflow.api;

import lombok.Getter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

// Item 92: the path half of the cross-prefix pair, reachable only through Lombok's
// class-level @Getter — the same binding layer item 91 built.
@Getter
@Component
public class PathCfg {

    @Value("${search.list.path}")
    private String searchListPath;
}
