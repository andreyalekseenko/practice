package com.example.containers.gateway.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

/**
 * Создаёт {@link RestClient} для обращения к Python-сервису.
 * Базовый URL и таймауты берутся из {@link GatewayProperties}.
 */
@Configuration
@EnableConfigurationProperties(GatewayProperties.class)
public class RestClientConfig {

    @Bean
    public RestClient pythonRestClient(GatewayProperties properties) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(properties.timeoutMs());
        factory.setReadTimeout(properties.timeoutMs());

        return RestClient.builder()
                .baseUrl(properties.pythonServiceUrl())
                .requestFactory(factory)
                .build();
    }
}
