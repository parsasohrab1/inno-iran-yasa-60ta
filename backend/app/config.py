from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./iyr.db"
    redis_url: str = "redis://localhost:6379/0"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    jwt_secret: str = "change-me"
    confidence_threshold: float = 0.7  # FR-2-8
    review_margin: float = 0.15  # confidence in [threshold, threshold+margin) -> Review


settings = Settings()
