package com.example.orderflow.shared;

/** Shared by orders-api and orders-worker. A change here reaches both. */
public enum OrderStatus {
    PLACED,
    RESERVED,
    SHIPPED,
    CANCELLED
}
