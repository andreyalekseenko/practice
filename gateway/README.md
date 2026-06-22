# Java-шлюз распознавания контейнеров (Spring Boot)

Java-слой системы: **API Gateway / BFF** (Backend for Frontend) на **Spring Boot**,
который стоит перед Python-сервисом распознавания и является единой точкой входа
для браузера.

## Зачем нужен Java-слой

```
                       ┌──────────────────────────────────────┐
  Браузер / 1С  ─────► │  Java Spring Boot (gateway) :8080     │
                       │  • отдаёт фронтенд (PWA)              │
                       │  • проксирует /api/**                 │
                       │  • типизированный /api/java/**        │
                       │  • /actuator/health                  │
                       └───────────────┬──────────────────────┘
                                       │ HTTP
                       ┌───────────────▼──────────────────────┐
                       │  Python FastAPI (detector) :8000      │
                       │  • YOLO / OpenVINO                    │
                       │  • SMB, журнал, датасет              │
                       └──────────────────────────────────────┘
```

- **Единая точка входа.** Браузер обращается только к шлюзу (порт 8080); ML-сервис
  не выставлен наружу.
- **Фронтенд без изменений.** Существующий PWA-фронтенд зовёт относительные пути
  `/api/...`, поэтому прозрачное проксирование работает «из коробки».
- **Java-домен поверх модели.** Эндпоинт `/api/java/**` возвращает чистые DTO
  (с вычисленными шириной/высотой/площадью рамок) вместо «legacy»-формата модели.

## Структура

| Файл | Назначение |
|------|------------|
| `GatewayApplication.java` | Точка входа Spring Boot |
| `config/GatewayProperties.java` | Типобезопасные настройки (`gateway.*`) |
| `config/RestClientConfig.java` | `RestClient` к Python-сервису (таймауты, base URL) |
| `proxy/ProxyController.java` | Прозрачный обратный прокси `/api/**` |
| `detection/DetectionController.java` | Типизированный REST `/api/java/v1/**` |
| `detection/DetectionService.java` | Вызов модели и маппинг ответа в DTO |
| `detection/dto/*.java` | DTO: запрос, рамка контейнера, результат |
| `detection/ApiExceptionHandler.java` | Валидация запроса → аккуратный JSON 400 |

## Эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Фронтенд (PWA), отдаётся шлюзом |
| `*` | `/api/**` | Прозрачно проксируется в Python-сервис |
| `POST` | `/api/java/v1/detect-link` | Типизированное распознавание по ссылке (DTO-ответ) |
| `GET` | `/actuator/health` | Проверка состояния шлюза |

### Пример типизированного запроса

```bash
curl -X POST http://localhost:8080/api/java/v1/detect-link \
  -H 'Content-Type: application/json' \
  -d '{"linkPhoto": "\\\\fs\\share\\Photo_41209.jpg", "login": "student"}'
```

Ответ:

```json
{
  "success": true,
  "sourceLink": "\\\\fs\\share\\Photo_41209.jpg",
  "totalContainers": 2,
  "containers": [
    {"classId":0,"confidence":0.91,"x0":10,"y0":20,"x1":210,"y1":420,
     "width":200,"height":400,"area":80000}
  ],
  "error": ""
}
```

## Запуск

### Весь стек через Docker Compose (из корня репозитория)

```bash
docker compose up --build
# открыть http://localhost:8080
```

### Только шлюз (Python-сервис уже запущен отдельно)

```bash
cd gateway
GATEWAY_PYTHON_SERVICE_URL=http://localhost:8000 mvn spring-boot:run
```

## Настройки (`gateway.*`)

| Свойство | Переменная окружения | По умолчанию |
|----------|----------------------|--------------|
| `gateway.python-service-url` | `GATEWAY_PYTHON_SERVICE_URL` | `http://localhost:8000` |
| `gateway.timeout-ms` | `GATEWAY_TIMEOUT_MS` | `60000` |

## Стек

- Java 17, Spring Boot 3.3 (Web, Actuator, Validation)
- `RestClient` для синхронного обращения к Python-сервису
- Maven, многостадийная сборка в Docker (JDK для сборки → JRE для запуска)
- JUnit 5 (тесты доменной логики DTO)
