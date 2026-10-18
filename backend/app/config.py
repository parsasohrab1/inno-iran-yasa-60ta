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
    plant_sim_enabled: bool = True  # SIMULATED plant data; turn off when a real PLC/SCADA adapter is attached
    plant_sim_tick_s: float = 1.0
    plant_lines: int = 4
    plant_contacts_path: str = "data/plant_contacts.json"  # git-ignored: real phone numbers / webhooks
    plant_source: str = "sim"  # sim | opcua
    plant_opcua_map_path: str = "data/opcua_map.json"
    sms_provider: str = ""  # "" (off) | kavenegar | generic; the API key is read from the SMS_API_KEY environment variable
    sms_sender: str = ""
    sms_min_severity: str = "critical"
    sms_generic_url: str = ""
    plant_shifts_path: str = "data/plant_shifts.json"  # the factory's real shift hours (default = assumed 3x8)


settings = Settings()
