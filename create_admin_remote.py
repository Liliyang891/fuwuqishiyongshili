import sys
sys.path.insert(0, "/app")
import auth
user = auth.register_user("admin", "admin123456")
auth.update_user_role(user["id"], "super_admin", None)
print("Super admin created: " + user["username"])
