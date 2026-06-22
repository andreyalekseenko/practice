package com.example.containers.gateway.detection.dto;

import java.util.List;

/**
 * Результат распознавания в чистом виде для потребителей Java-API.
 *
 * @param success         успешно ли отработала модель
 * @param sourceLink      исходный путь к фото
 * @param totalContainers количество найденных контейнеров
 * @param containers      список рамок
 * @param error           текст ошибки (пустой при успехе)
 */
public record DetectionResult(
        boolean success,
        String sourceLink,
        int totalContainers,
        List<DetectedContainer> containers,
        String error
) {
    public static DetectionResult failure(String sourceLink, String error) {
        return new DetectionResult(false, sourceLink, 0, List.of(), error);
    }
}
