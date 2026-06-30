import os
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_smorest import Api
from flask_cors import CORS

from src.order_service.extentions.db import db
# Import all models to register them with SQLAlchemy
from src.order_service.models.order_model import OrderModel
from src.order_service.resources.orders import blp as OrderBp

# This is called factory pattern

# db_user = Resources.config.DB_USER
# db_pass = Resources.config.DB_PASS
# db_name = Resources.config.DB_NAME
# db_host = Resources.config.DB_HOST
def create_app(db_url=None):
    order_service = Flask(__name__)
    order_service.config["CORS_AUTOMATIC_OPTIONS"] = True
    order_service.config["PROPAGATE_EXCEPTIONS"] = True
    order_service.config["API_TITLE"] = "Order service API"
    order_service.config["API_VERSION"] = "v1"
    order_service.config["OPENAPI_VERSION"] = "3.0.3"
    order_service.config["OPENAPI_URL_PREFIX"] = "/"
    order_service.config['TESTING'] = True
    order_service.config["OPENAPI_SWAGGER_UI_PATH"] = "/swagger-ui"
    order_service.config["OPENAPI_SWAGGER_UI_URL"] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"
    order_service.config['TESTING'] = True  # Enable testing mode for the Flask app
    
    # Configure CORS for production
    allowed_origins = os.getenv('ALLOWED_ORIGINS', '*').split(",")
    allowed_origins = [origin.strip() for origin in allowed_origins]
    
    CORS(
    order_service,
    origins=allowed_origins,
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    intercept_exceptions=True
    )

    """ 
        for SQLite, local database file is created in the data directory of the store service application.
        This is useful for development and testing purposes, as it allows the application to run without needing an 
        external database server. (not to be used in production)
    """
    # store_service.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///data/store_data.db"

    # *** for SQLite - app directory is created inside container for data persistence but containers are ephemeral ***
    # store_service.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////app/store_service/src/store_service/data/store_data.db"

    """
        for MySQL, the database is created in a separate container and the store service container connects to it 
        using the MySQL driver.
        Note: The database connection string is in the format:
        "mysql+pymysql://<username>:<password>@<host>/<database_name>"
        where:
        - <username> is the MySQL username
        - <password> is the MySQL password
        - <host> is the hostname or IP address of the MySQL server
        - <database_name> is the name of the database to connect to
        store_service.config["SQLALCHEMY_DATABASE_URI"] = "mysql+pymysql://store_user:store_pass@mysql_store:3306/store_db"
    """

    SQLALCHEMY_DATABASE_URI = db_url or (
        f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{os.getenv('MYSQL_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('MYSQL_DATABASE')}"
    )
    print("Connecting to DB:", SQLALCHEMY_DATABASE_URI)  # helpful for debugging

    order_service.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    db.init_app(order_service)  # db is SQLAlchemy extension

    with order_service.app_context():
        db.create_all()

    api = Api(order_service)

    """
        Note: As the store service is consuming (validating) JWT tokens, the following code configures JWT 
        (JSON Web Token) handling for the store service. JWT is a compact, URL-safe means of representing 
        claims to be transferred securely between two parties.
    
        According to JWT best practices and documentation:
        1. Assign a secret key (JWT_SECRET_KEY) that matches the issuing service's key. 
        This key is used to verify and trust that incoming JWT tokens were genuinely issued by the correct application.
        2. Initialize JWTManager with the Flask application; it handles JWT tokens and their validation lifecycle.
        3. JWT_SECRET_KEY is used to verify the cryptographic signature of incoming JWT tokens —
        this validation ensures that the token has not been forged or tampered with.
        4. JWT_TOKEN_LOCATION specifies where the application should look for JWT tokens in each request 
        (e.g., headers, cookies).
    
        *** To check if a token is valid and not forged or tampered with, the service recalculates the token's 
        cryptographic signature using the shared secret key. The newly computed signature is compared with the 
        signature part of the token; if they match, the token is authentic and untampered.
        (Note: JWT signatures are verified, not decrypted—JWT does not use decryption for this process.)
        ***
    """

    order_service.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY")
    order_service.config["JWT_TOKEN_LOCATION"] = ["headers"]
    JWTManager(order_service)

    # @store_service.before_request
    # def create_tables():
    #     db.create_all()

    @order_service.route("/health")
    def health():
        return jsonify({"status": "healthy"}), 200

    api.register_blueprint(OrderBp)

    return order_service