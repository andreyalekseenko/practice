# --- Сборка (Maven + JDK 17) ---
# ВАЖНО: контекст сборки — корень репозитория (см. docker-compose.yml),
# поэтому пути начинаются с gateway/ и static/.
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build

# Сначала только pom.xml — кэшируем загрузку зависимостей между сборками.
COPY gateway/pom.xml .
RUN mvn -B -q dependency:go-offline

# Исходники Java + фронтенд PWA, который шлюз будет отдавать как статику.
COPY gateway/src ./src
COPY static ./src/main/resources/static

RUN mvn -B -q clean package -DskipTests

# --- Запуск (только JRE) ---
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /build/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
