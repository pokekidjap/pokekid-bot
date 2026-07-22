from services.bot_db import (
    get_admins,
    get_admin,
    is_admin,
    is_owner,
)


print("ADMIN ATTIVI:")
print(get_admins())

print("\nDAVIDE:")
print(get_admin(785011428))

print("\nÈ ADMIN:")
print(is_admin(785011428))

print("\nÈ OWNER:")
print(is_owner(785011428))
