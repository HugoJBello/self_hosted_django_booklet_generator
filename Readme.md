

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

## Proxy inverso Nginx bajo `/pdf_manager/`

El proyecto está preparado para publicarse conservando el prefijo
`/pdf_manager` (por ejemplo, `https://example.com/pdf_manager/`). El contenedor
web escucha en el puerto `8000` y el `docker-compose.yml` lo publica en el
puerto `8010` del host.

Añada lo siguiente dentro del bloque `server { ... }` de Nginx:

```nginx
# Normaliza la URL sin barra final.
location = /pdf_manager {
    return 301 /pdf_manager/;
}

location ^~ /pdf_manager/ {
    # Sin barra final: el upstream debe recibir /pdf_manager/... completo.
    proxy_pass http://127.0.0.1:8010;

    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # La generación y el OCR pueden tardar, y se suben archivos PDF grandes.
    # Este límite debe ser igual o menor que el permitido por cualquier proxy
    # anterior (CDN, balanceador o túnel) para evitar respuestas HTTP 413.
    client_max_body_size 200M;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_request_buffering off;
}
```

La ausencia de la barra final en `proxy_pass http://127.0.0.1:8010` es
intencionada. Si se escribe `proxy_pass http://127.0.0.1:8010/`, Nginx elimina
el prefijo antes de enviar la petición y Django responderá con rutas incorrectas
o errores 404.

Mantenga estas variables en los servicios `pdf_manager` y
`pdf_manager_worker`:

```yaml
environment:
  APP_SUBPATH: "/pdf_manager"
  DJANGO_ALLOWED_HOSTS: "example.com"
  DJANGO_CSRF_TRUSTED_ORIGINS: "https://example.com"
  DJANGO_COOKIE_SECURE: "True"
```

`APP_SUBPATH` controla las URLs de archivos estáticos y media. En esta versión,
las rutas Django también contienen literalmente `pdf_manager`, por lo que para
usar otro prefijo no basta con cambiar la variable: hay que modificar asimismo
`pdf_manager_project/urls.py`.

Tras editar la configuración, valide y recargue Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Si aparece `413 Payload Too Large`, compruebe la configuración efectiva con
`nginx -T`: una directiva `client_max_body_size` dentro de `location` prevalece
sobre la definida en `server`. Si hay otro proxy o CDN delante de Nginx, su
límite de carga también debe admitir el tamaño deseado.

Finalmente, compruebe tanto la aplicación como un recurso estático:

```bash
curl -I https://example.com/pdf_manager/
curl -I https://example.com/pdf_manager/static/booklets/favicon.svg
```

## Impresión mediante CUPS

La sección **Print** permite enviar un PDF subido o un resultado del historial a una
cola persistida en Django. Solo los administradores pueden crear o modificar
impresoras. Al guardar, la configuración se aplica a CUPS y se consultan las
opciones que anuncia el driver. Cada trabajo conserva usuario, documento, opciones,
resultado e identificador CUPS.

El despliegue incluye su propio servicio CUPS: no utiliza ni exige modificar el
CUPS del host. El servicio comparte un socket privado con Django y usa red de host
para recibir anuncios mDNS/Bonjour y alcanzar impresoras IPP de la LAN. Sus colas,
PPD, trabajos y caché persisten bajo `data/cups/`. No publica la administración
CUPS en ningún puerto de red.

La detección automática funciona con impresoras que anuncian IPP/IPP Everywhere,
Bonjour o un backend reconocido por CUPS. Las impresoras en otra VLAN que bloquee
mDNS pueden añadirse igualmente con su URI `ipp://` o `ipps://`.

## Persistencia y copias de seguridad

Todo el estado persistente vive en `./data`: la base de datos en `data/db.sqlite3`,
los PDFs en `data/media` y Redis en `data/redis`. El directorio completo está
excluido de Git. Para una copia consistente:

```bash
docker compose stop
tar -czf pdf-manager-backup-$(date +%F).tar.gz data/
docker compose start
```

Para restaurar, detenga los contenedores, sustituya `data/` por el contenido del
archivo y vuelva a iniciarlos. El backup incluye documentos, impresoras,
configuraciones e historial.
