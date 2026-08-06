package com.example.orderflow.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Class mapping + method mapping + context-path concatenate into one endpoint. */
@RestController
@RequestMapping("/v1")
public class OrderController {

    @GetMapping("/list")
    public String list() {
        return "[]";
    }

    @GetMapping("/detail/{orderId}")
    public String detail(String orderId) {
        return "{}";
    }
}
