package com.example.orderflow.api;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

// Item 92: the host and the path live under prefixes no naming rule relates; the ONLY
// place they meet is this call's argument, so the pair must be read off the code —
// a same-class @Value field concatenated with another class's getter.
@Component
public class ReportsClient {

    @Value("${reporting.host}")
    private String reportingHost;

    private PathCfg pathCfg;
    private RestTemplate restTemplate;

    public String fetch() {
        return restTemplate.getForObject(reportingHost + pathCfg.getSearchListPath(),
                String.class);
    }
}
