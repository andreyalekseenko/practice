package com.example.containers.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Настройки шлюза, привязанные к префиксу {@code gateway.*} в application.yml
 * (переопределяются переменными окружения, например {@code GATEWAY_PYTHON_SERVICE_URL}).
 *
 * @param pythonServiceUrl базовый URL Python-сервиса распознавания
 * @param timeoutMs        таймаут обращения к Python-сервису, миллисекунды
 */
@ConfigurationProperties(prefix = "gateway")
public record GatewayProperties(
        String pythonServiceUrl,
        int timeoutMs
) {
}
