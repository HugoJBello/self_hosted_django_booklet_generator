

# PDF Manager

Aplicación privada para crear y procesar documentos PDF. Todas las herramientas requieren iniciar sesión; no existe registro público.

## Primer acceso

Al arrancar, después de aplicar las migraciones, el contenedor ejecuta `python manage.py ensure_admin`. El comando crea el usuario administrador inicial únicamente si no existe:

- Usuario: `admin`
- Contraseña: `change_me`

La contraseña debe cambiarse inmediatamente desde el menú de usuario. Si `admin` ya existe, el arranque **nunca modifica su contraseña**. Las credenciales iniciales se pueden personalizar antes del primer arranque con `DJANGO_INITIAL_ADMIN_USERNAME` y `DJANGO_INITIAL_ADMIN_PASSWORD`.

Para instalaciones locales existentes, ejecutar una vez:

```bash
python manage.py migrate
python manage.py ensure_admin
```

Los administradores pueden crear usuarios, borrar usuarios y restablecer contraseñas desde **Users**. Los usuarios normales solo pueden cambiar su propia contraseña.

## En local

en una terminal a parte

 python manage.py rqworker default

en otra term

 python manage.py runserver


## En Docker

 docker compose up -d --build --force-recreate
