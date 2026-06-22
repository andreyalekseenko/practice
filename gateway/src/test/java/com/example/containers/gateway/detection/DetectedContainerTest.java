package com.example.containers.gateway.detection;

import com.example.containers.gateway.detection.dto.DetectedContainer;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class DetectedContainerTest {

    @Test
    void computesWidthHeightAndArea() {
        DetectedContainer c = DetectedContainer.of(0, 0.91, 10, 20, 110, 220);

        assertEquals(100, c.width());
        assertEquals(200, c.height());
        assertEquals(20_000L, c.area());
    }

    @Test
    void clampsNegativeDimensionsToZero() {
        // Перепутанные углы не должны давать отрицательные размеры.
        DetectedContainer c = DetectedContainer.of(1, 0.5, 100, 100, 40, 40);

        assertEquals(0, c.width());
        assertEquals(0, c.height());
        assertEquals(0L, c.area());
    }
}
