package com.example.containers.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Точка входа Java-шлюза.
 *
 * <p>Шлюз решает три задачи:
 * <ul>
 *     <li>отдаёт статический фронтенд (PWA) на корне {@code /};</li>
 *     <li>прозрачно проксирует запросы {@code /api/**} в Python-сервис распознавания;</li>
 *     <li>предоставляет собственный типизированный REST {@code /api/java/**}
 *         с доменными DTO поверх «сырого» ответа модели.</li>
 * </ul>
 */
@SpringBootApplication
public class GatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(GatewayApplication.class, args);
    }
}
