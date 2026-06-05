import uuid
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from itsdangerous import URLSafeTimedSerializer
import config

class User(UserMixin):
    def __init__(self, id, username, password_hash, role='user', email=None, is_verified=0):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.email = email
        self.is_verified = bool(is_verified)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def _get_db():
        conn = psycopg2.connect(config.DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn

    @staticmethod
    def _get_cursor(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    @staticmethod
    def get(user_id):
        conn = User._get_db()
        cursor = User._get_cursor(conn)
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        conn.close()
        if not user_data:
            return None
        return User(
            id=user_data['id'],
            username=user_data['username'],
            password_hash=user_data['password_hash'],
            role=user_data['role'],
            email=user_data['email'],
            is_verified=user_data['is_verified']
        )

    @staticmethod
    def find_by_username_or_email(identifier):
        conn = User._get_db()
        cursor = User._get_cursor(conn)
        cursor.execute("SELECT * FROM users WHERE username = %s OR email = %s", (identifier, identifier))
        user_data = cursor.fetchone()
        conn.close()
        if not user_data:
            return None
        return User(
            id=user_data['id'],
            username=user_data['username'],
            password_hash=user_data['password_hash'],
            role=user_data['role'],
            email=user_data['email'],
            is_verified=user_data['is_verified']
        )

    @staticmethod
    def create(username, password, email, role=None):
        if User.find_by_username_or_email(username) or User.find_by_username_or_email(email):
            return None

        conn = User._get_db()
        cursor = User._get_cursor(conn)

        if role is None:
            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()['count']
            role = 'admin' if count == 0 else 'user'

        new_id = str(uuid.uuid4())
        hashed_password = generate_password_hash(password)

        try:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, role, is_verified) VALUES (%s, %s, %s, %s, %s, 0)",
                (new_id, username, email, hashed_password, role)
            )
            conn.commit()
            return User(new_id, username, hashed_password, role, email, 0)
        except Exception as e:
            conn.rollback()
            print(f"Create User Error: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def verify_user(email):
        conn = User._get_db()
        cursor = User._get_cursor(conn)
        try:
            cursor.execute("UPDATE users SET is_verified = 1 WHERE email = %s", (email,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def get_verification_token(self):
        serializer = URLSafeTimedSerializer(config.SECRET_KEY)
        return serializer.dumps(self.email, salt=config.SECURITY_PASSWORD_SALT)

    @staticmethod
    def verify_token(token, expiration=3600):
        serializer = URLSafeTimedSerializer(config.SECRET_KEY)
        try:
            email = serializer.loads(token, salt=config.SECURITY_PASSWORD_SALT, max_age=expiration)
            return email
        except Exception:
            return None

    @staticmethod
    def find_by_email(email):
        """僅透過 Email 查詢用戶 (用於忘記密碼)"""
        conn = User._get_db()
        cursor = User._get_cursor(conn)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user_data = cursor.fetchone()
        conn.close()
        if not user_data:
            return None
        return User(
            id=user_data['id'],
            username=user_data['username'],
            password_hash=user_data['password_hash'],
            role=user_data['role'],
            email=user_data['email'],
            is_verified=user_data['is_verified']
        )

    @staticmethod
    def reset_password(email, new_password):
        """重設用戶密碼 (用於忘記密碼流程)"""
        conn = User._get_db()
        cursor = User._get_cursor(conn)
        try:
            hashed = generate_password_hash(new_password)
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE email = %s",
                (hashed, email)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            print(f"Reset password error: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def update_profile(user_id, **kwargs):
        """管理員編輯用戶資料 (username, email, password)"""
        conn = User._get_db()
        cursor = User._get_cursor(conn)
        try:
            updates = []
            values = []

            if 'username' in kwargs:
                # 檢查用戶名是否已被其他用戶使用
                cursor.execute(
                    "SELECT id FROM users WHERE username = %s AND id != %s",
                    (kwargs['username'], user_id)
                )
                if cursor.fetchone():
                    conn.close()
                    return False, '用戶名稱已被使用'

                updates.append("username = %s")
                values.append(kwargs['username'])

            if 'email' in kwargs:
                # 檢查 Email 是否已被其他用戶使用
                cursor.execute(
                    "SELECT id FROM users WHERE email = %s AND id != %s",
                    (kwargs['email'], user_id)
                )
                if cursor.fetchone():
                    conn.close()
                    return False, 'Email 已被使用'

                updates.append("email = %s")
                values.append(kwargs['email'])

            if 'password' in kwargs and kwargs['password']:
                updates.append("password_hash = %s")
                values.append(generate_password_hash(kwargs['password']))

            if not updates:
                conn.close()
                return False, '沒有要更新的欄位'

            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = %s"
            values.append(user_id)
            cursor.execute(sql, values)
            conn.commit()
            return True, '更新成功'
        except Exception as e:
            conn.rollback()
            print(f"Update profile error: {e}")
            return False, str(e)
        finally:
            conn.close()

    def get_password_reset_token(self):
        """產生密碼重設 token (30 分鐘有效)"""
        serializer = URLSafeTimedSerializer(config.SECRET_KEY)
        return serializer.dumps(self.email, salt='password-reset-salt')

    @staticmethod
    def verify_password_reset_token(token, expiration=1800):
        """驗證密碼重設 token (預設 30 分鐘)"""
        serializer = URLSafeTimedSerializer(config.SECRET_KEY)
        try:
            email = serializer.loads(token, salt='password-reset-salt', max_age=expiration)
            return email
        except Exception:
            return None
