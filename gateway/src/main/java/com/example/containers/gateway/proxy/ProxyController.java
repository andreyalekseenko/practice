package com.example.containers.gateway.proxy;

import com.example.containers.gateway.config.GatewayProperties;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Set;

/**
 * Прозрачный обратный прокси для всех путей {@code /api/**}.
 *
 * <p>Запрос фронтенда уходит «как есть» (метод, query, тело, Content-Type) в Python-сервис,
 * а ответ возвращается без изменений. За счёт этого существующий фронтенд работает без правок,
 * но весь трафик проходит через Java-слой, где его можно логировать, ограничивать и расширять.
 *
 * <p>Более специфичный путь {@code /api/java/**} обрабатывается отдельным контроллером
 * (Spring выбирает наиболее точный шаблон), поэтому сюда он не попадает.
 */
@RestController
public class ProxyController {

    private static final Logger log = LoggerFactory.getLogger(ProxyController.class);

    /** Заголовки, которые нельзя пробрасывать «насквозь» — их выставит сам HTTP-клиент. */
    private static final Set<String> HOP_BY_HOP = Set.of(
            "host", "connection", "content-length", "transfer-encoding", "keep-alive", "te", "upgrade"
    );

    private final RestClient pythonRestClient;
    private final String pythonBaseUrl;

    public ProxyController(RestClient pythonRestClient, GatewayProperties properties) {
        this.pythonRestClient = pythonRestClient;
        // Базовый URL без хвостового слэша — пути уже начинаются с "/api/...".
        String base = properties.pythonServiceUrl();
        this.pythonBaseUrl = base.endsWith("/") ? base.substring(0, base.length() - 1) : base;
    }

    @RequestMapping("/api/**")
    public ResponseEntity<byte[]> proxy(HttpServletRequest request,
                                        @RequestBody(required = false) byte[] body) {
        String path = request.getRequestURI();
        String query = request.getQueryString();
        String target = query == null ? path : path + "?" + query;
        HttpMethod method = HttpMethod.valueOf(request.getMethod());

        // Строим абсолютный URI вручную: путь/строка запроса уже закодированы контейнером,
        // поэтому RestClient не должен трактовать их как URI-шаблон ("{...}").
        URI uri = URI.create(pythonBaseUrl + target);

        try {
            RestClient.RequestBodySpec spec = pythonRestClient
                    .method(method)
                    .uri(uri)
                    .headers(headers -> copyRequestHeaders(request, headers));

            if (body != null && body.length > 0) {
                spec.body(body);
            }

            ResponseEntity<byte[]> response = spec.retrieve().toEntity(byte[].class);
            log.debug("Проксирование {} {} -> {}", method, target, response.getStatusCode());
            return sanitize(response);

        } catch (RestClientResponseException e) {
            // Python вернул HTTP-ошибку (4xx/5xx) — пробрасываем её тело и статус как есть.
            return ResponseEntity.status(e.getStatusCode())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(e.getResponseBodyAsByteArray());
        } catch (Exception e) {
            // Сервис недоступен / таймаут — отдаём осмысленный 502.
            log.warn("Python-сервис недоступен для {} {}: {}", method, target, e.getMessage());
            String msg = "{\"status\":false,\"error\":\"detection service unavailable: "
                    + e.getMessage() + "\"}";
            return ResponseEntity.status(502)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(msg.getBytes(StandardCharsets.UTF_8));
        }
    }

    private void copyRequestHeaders(HttpServletRequest request, HttpHeaders out) {
        var names = request.getHeaderNames();
        while (names.hasMoreElements()) {
            String name = names.nextElement();
            if (HOP_BY_HOP.contains(name.toLowerCase())) {
                continue;
            }
            request.getHeaders(name).asIterator().forEachRemaining(v -> out.add(name, v));
        }
    }

    /** Убирает hop-by-hop заголовки из ответа Python, чтобы не сломать соединение с браузером. */
    private ResponseEntity<byte[]> sanitize(ResponseEntity<byte[]> response) {
        HttpHeaders out = new HttpHeaders();
        response.getHeaders().forEach((name, values) -> {
            if (!HOP_BY_HOP.contains(name.toLowerCase())) {
                out.put(name, List.copyOf(values));
            }
        });
        return new ResponseEntity<>(response.getBody(), out, response.getStatusCode());
    }
}
