from services.bot_db import (
    get_active_shipping_methods,
    get_paypal_email,
)

print("CORRIERI:")
print(get_active_shipping_methods())

print("\nPAYPAL:")
print(get_paypal_email())