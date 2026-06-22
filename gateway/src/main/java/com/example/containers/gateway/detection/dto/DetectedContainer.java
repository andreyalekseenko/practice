package com.example.containers.gateway.detection.dto;

/**
 * Один распознанный контейнер с нормализованными полями.
 * В отличие от «legacy»-формата Python-сервиса здесь поля названы понятно,
 * а ширина/высота/площадь вычислены на Java-стороне.
 *
 * @param classId    идентификатор класса модели
 * @param confidence уверенность модели в диапазоне 0..1
 * @param x0         левая граница рамки, px
 * @param y0         верхняя граница рамки, px
 * @param x1         правая граница рамки, px
 * @param y1         нижняя граница рамки, px
 * @param width      ширина рамки, px
 * @param height     высота рамки, px
 * @param area       площадь рамки, px²
 */
public record DetectedContainer(
        int classId,
        double confidence,
        int x0,
        int y0,
        int x1,
        int y1,
        int width,
        int height,
        long area
) {
    /** Фабрика из координат углов: производные поля считаются автоматически. */
    public static DetectedContainer of(int classId, double confidence, int x0, int y0, int x1, int y1) {
        int width = Math.max(0, x1 - x0);
        int height = Math.max(0, y1 - y0);
        return new DetectedContainer(classId, confidence, x0, y0, x1, y1, width, height, (long) width * height);
    }
}
