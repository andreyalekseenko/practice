package com.example.containers.gateway.detection;

import com.example.containers.gateway.detection.dto.DetectLinkRequest;
import com.example.containers.gateway.detection.dto.DetectionResult;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Собственный типизированный REST Java-слоя.
 *
 * <p>Путь {@code /api/java/**} специфичнее, чем {@code /api/**} у прокси,
 * поэтому Spring направляет запросы сюда, минуя прозрачное проксирование.
 */
@RestController
@RequestMapping("/api/java/v1")
public class DetectionController {

    private final DetectionService detectionService;

    public DetectionController(DetectionService detectionService) {
        this.detectionService = detectionService;
    }

    /**
     * Распознавание по ссылке с чистым, нормализованным ответом
     * (в отличие от «legacy»-формата Python-сервиса).
     */
    @PostMapping("/detect-link")
    public DetectionResult detectLink(@Valid @RequestBody DetectLinkRequest request) {
        return detectionService.detectByLink(request);
    }
}
