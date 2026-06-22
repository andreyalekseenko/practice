package com.example.containers.gateway.detection.dto;

import jakarta.validation.constraints.NotBlank;

/**
 * Запрос на распознавание контейнеров по пути к фото на сетевом ресурсе (SMB/UNC).
 *
 * @param linkPhoto путь к изображению (обязателен)
 * @param login     учётная запись инициатора (необязательно)
 */
public record DetectLinkRequest(
        @NotBlank(message = "linkPhoto не должен быть пустым")
        String linkPhoto,
        String login
) {
}
