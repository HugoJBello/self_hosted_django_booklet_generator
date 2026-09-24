

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

## Historial de actividad

Cada ejecución de Booklets, Join, Split, OCR, Diary y Calendar conserva en la base de datos:

- El usuario propietario y la fecha.
- Las opciones elegidas.
- Los archivos de entrada y los resultados generados.
- El estado necesario para reabrir el trabajo en su herramienta.

Cada herramienta muestra las últimas actividades del usuario. **View history** permite consultar su historial completo. Los administradores disponen además de **All activity** en el menú de usuario para auditar la actividad de todos los usuarios.

Entrar normalmente en una herramienta desde la navegación abre siempre un espacio de trabajo vacío. El estado anterior solo se carga cuando el usuario selecciona explícitamente una actividad y pulsa **Reopen**; las redirecciones internas de un mismo flujo conservan el trabajo mientras se está editando.

Las descargas se sirven mediante rutas autenticadas: un usuario solo puede consultar sus propios archivos; los administradores pueden consultar todos. Los archivos históricos viven bajo el volumen persistente configurado como `DJANGO_MEDIA_ROOT`, por lo que hay que incluir dicho volumen en las copias de seguridad junto con la base de datos.

## En local

en una terminal a parte

 python manage.py rqworker default

en otra term

 python manage.py runserver


## En Docker

 docker compose up -d --build --force-recreate
