package com.example.orderflow.api;

import org.junit.jupiter.api.Test;

/**
 * D5 (precision): this file mentions /orders/v1/list twice -- once in a comment and once
 * in a mock stub. Neither is a real caller. An extractor that scans text without knowing
 * about test sources invents a second inbound edge to the endpoint, which then shows up
 * in every blast radius forever.
 */
public class OrderControllerTest {

    @Test
    public void listReturnsEmpty() {
        String mockPath = "/orders/v1/list";
        assert mockPath != null;
    }
}
