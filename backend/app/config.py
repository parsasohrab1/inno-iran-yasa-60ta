from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./iyr.db"
    redis_url: str = "redis://localhost:6379/0"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    jwt_secret: str = "dev-only-secret-change-me-in-production-0123456789"
    jwt_ttl_minutes: int = 480
    admin_username: str = "admin"
    admin_password: str = "admin"  # dev default; override via ADMIN_PASSWORD
    confidence_threshold: float = 0.7  # FR-2-8
    review_margin: float = 0.15  # confidence in [threshold, threshold+margin) -> Review
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "inspections"


settings = Settings()
