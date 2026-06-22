package com.example.containers.gateway.detection;

import com.example.containers.gateway.detection.dto.DetectLinkRequest;
import com.example.containers.gateway.detection.dto.DetectedContainer;
import com.example.containers.gateway.detection.dto.DetectionResult;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Доменный сервис: вызывает Python-распознавание и приводит «legacy»-ответ
 * к чистым Java-DTO.
 *
 * <p>Формат ответа Python ({@code /api/v1/projects/process_link}):
 * <pre>
 * { "status": true,
 *   "error": "",
 *   "result": [ {"count": N, "orientation": {...}},
 *               {"key": classId, "ratio": conf, "x0":.., "y0":.., "x1":.., "y1":..}, ... ] }
 * </pre>
 * Первый элемент массива — сводка, остальные — рамки.
 */
@Service
public class DetectionService {

    private static final Logger log = LoggerFactory.getLogger(DetectionService.class);

    private final RestClient pythonRestClient;

    public DetectionService(RestClient pythonRestClient) {
        this.pythonRestClient = pythonRestClient;
    }

    public DetectionResult detectByLink(DetectLinkRequest request) {
        Map<String, Object> payload = Map.of(
                "link_photo", request.linkPhoto(),
                "login", request.login() == null ? "java-gateway" : request.login()
        );

        JsonNode response;
        try {
            response = pythonRestClient.post()
                    .uri("/api/v1/projects/process_link")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (Exception e) {
            log.warn("Ошибка вызова Python-сервиса для {}: {}", request.linkPhoto(), e.getMessage());
            return DetectionResult.failure(request.linkPhoto(), "detection service error: " + e.getMessage());
        }

        return mapResponse(request.linkPhoto(), response);
    }

    private DetectionResult mapResponse(String link, JsonNode response) {
        if (response == null) {
            return DetectionResult.failure(link, "empty response from detection service");
        }
        boolean status = response.path("status").asBoolean(false);
        String error = response.path("error").asText("");
        if (!status) {
            return DetectionResult.failure(link, error.isBlank() ? "detection failed" : error);
        }

        JsonNode result = response.path("result");
        List<DetectedContainer> containers = new ArrayList<>();
        int reportedCount = 0;

        if (result.isArray()) {
            for (int i = 0; i < result.size(); i++) {
                JsonNode node = result.get(i);
                if (i == 0) {
                    reportedCount = node.path("count").asInt(0);
                    continue;
                }
                containers.add(DetectedContainer.of(
                        node.path("key").asInt(0),
                        node.path("ratio").asDouble(0.0),
                        node.path("x0").asInt(0),
                        node.path("y0").asInt(0),
                        node.path("x1").asInt(0),
                        node.path("y1").asInt(0)
                ));
            }
        }

        // Доверяем фактическому числу рамок; сводный count оставляем как ориентир в логе.
        int total = containers.isEmpty() ? reportedCount : containers.size();
        return new DetectionResult(true, link, total, containers, "");
    }
}
