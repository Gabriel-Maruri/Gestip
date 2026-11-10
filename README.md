# Gestip — Plataforma de Gestión Personal

> Sistema integral para el control de entrenamiento, nutrición y finanzas personales en una arquitectura web progresiva (PWA), lineal y de alto rendimiento.

---

## 1. Visión del Proyecto

Gestip unifica los tres hábitos centrales del desarrollo individual en una sola interfaz sobria y funcional, eliminando la dispersión entre múltiples herramientas:

1. **Entrenamiento**: Planificación por divisiones, registro de series con sobrecarga progresiva, cálculo automático de marcas personales (PR) y temporizador de recuperación integrado.
2. **Nutrición**: Registro ágil por momentos del día (Desayuno, Almuerzo, Cena, Snack), catálogo depurado de más de 230 alimentos en español con conversión porción/gramos, control de macronutrientes y registro de hidratación.
3. **Finanzas**: Control de ingresos y egresos por categorías, presupuestos mensuales con semáforo de alerta, metas de ahorro, transacciones fijas recurrentes y exportación en formato CSV.

Diseñado bajo principios de diseño lineal (estilo Linear/Stripe), sin elementos distractores ni saturación visual, garantizando máxima velocidad de carga e instalación nativa como PWA en dispositivos móviles (Android e iOS).

---

## 2. Arquitectura Técnica

- **Backend**: Python 3 con Flask, arquitectura modular de rutas y gestión de sesiones firmadas.
- **Base de Datos**: PostgreSQL relacional con esquema optimizado en tercera forma normal (`schema.sql`).
- **Seguridad**: Autenticación multiusuario con hash Scrypt (`werkzeug.security`), cookies de sesión `HttpOnly` y `SameSite=Lax`, claves criptográficas de 256 bits y aislamiento estricto por `user_id` a nivel de consulta.
- **Frontend**: HTML5 semántico, CSS Grid responsivo (1400px máx), Web Audio API para alertas sonoras y Chart.js integrado localmente (sin dependencias externas en tiempo de ejecución).
- **Móvil / PWA**: Web App Manifest (`manifest.json`) y Service Worker (`sw.js`) con soporte para funcionamiento offline de recursos estáticos e instalación en pantalla de inicio.
- **Despliegue**: Compatible con contenedores y plataformas cloud como Render, Railway y Supabase Database.

---

## 3. Funcionalidades Detalladas

### Módulo de Entrenamiento
- **Estructura por divisiones**: Organización de ejercicios por grupos musculares o días de rutina en el 70% del ancho de pantalla.
- **Registro de series reales**: Captura de peso (kg) y repeticiones por serie (`S1`, `S2`, `S3...`).
- **Récords Personales (PR)**: Detección automática y distintivo visual cuando se supera la carga histórica en un ejercicio.
- **Temporizador de descanso**: Intervalos predefinidos (45s, 60s, 90s, 120s) con notificación sonora sintética mediante Web Audio API.
- **Mini-calendario interactivo**: Vista compacta de 7 días con marcado automático de asistencia al registrar la primera serie.

### Módulo de Nutrición
- **Catálogo de 234 alimentos**: Base de datos depurada en español, clasificada por grupos de alimentos.
- **Unidades prácticas y gramos**: Alternancia rápida entre unidades habituales (ej. manzana, huevo) y pesaje en gramos.
- **Momentos del día y subtotales**: Agrupación visual en Desayuno, Almuerzo, Cena y Snack con desglose calórico y proteico.
- **Metas de macronutrientes**: Barras de progreso proporcionales para Calorías, Proteínas, Carbohidratos y Grasas.
- **Control de hidratación**: Registro de ingesta de agua en incrementos de 250 ml.
- **Recetas frecuentes**: Creación de platos combinados con inserción diaria en un solo clic.

### Módulo de Finanzas
- **Balance financiero**: Resumen inmediato de Ingresos, Egresos y Saldo Neto mensual.
- **Presupuestos con alertas tricolor**:
  - Verde: Consumo inferior al 80% del límite asignado.
  - Amarillo: Consumo entre el 80% y el 99% (alerta preventiva).
  - Rojo: Consumo igual o superior al 100% (límite excedido).
- **Metas de ahorro**: Registro de fondos con seguimiento porcentual de avance.
- **Transacciones fijas**: Automatización de cobros o pagos recurrentes con aplicación en un solo paso.
- **Exportación CSV**: Descarga instantánea de movimientos compatible con Excel y software contable.
- **Cierre mensual automático**: Limpieza preventiva de transacciones del período anterior al iniciar un nuevo mes.

---

## 4. Estructura del Repositorio

```text
gestip/
├── app.py                 # Aplicación principal Flask y controladores de ruta
├── schema.sql             # Definición del esquema DDL de PostgreSQL
├── cargar_alimentos.py    # Script de carga inicial del catálogo de alimentos
├── alimentos.csv          # Catálogo depurado de alimentos y macronutrientes
├── requirements.txt       # Dependencias de producción (Flask, psycopg2-binary, gunicorn)
├── Procfile               # Instrucción de ejecución para entornos cloud (Render)
├── iniciar.bat            # Lanzador local para entorno Windows
├── .env.example           # Plantilla de variables de entorno para despliegue
├── .gitignore             # Exclusión de credenciales y archivos temporales
├── static/
│   ├── style.css          # Sistema de diseño lineal y paleta Slate
│   ├── chart.min.js       # Librería de gráficos servida localmente
│   ├── manifest.json      # Configuración PWA
│   ├── sw.js              # Service Worker para caché y PWA
│   └── icon-*.png         # Iconografía para dispositivos móviles
└── templates/
    ├── base.html          # Plantilla base con barra de navegación y PWA prompt
    ├── login.html         # Formulario de autenticación
    ├── registro.html      # Registro de nuevos usuarios
    ├── entrenamiento.html # Vista del módulo de entrenamiento
    ├── alimentacion.html  # Vista del módulo de nutrición
    └── finanzas.html      # Vista del módulo financiero
```

---

## 5. Guía de Instalación y Ejecución Local

### Prerrequisitos
- Python 3.10 o superior
- Servidor PostgreSQL activo

### Pasos
1. Clonar el repositorio:
   ```bash
   git clone https://github.com/tu-usuario/gestip.git
   cd gestip
   ```

2. Instalar las dependencias:
   ```bash
   pip install -r requirements.txt
   ```

3. Configurar las variables de entorno:
   Copie el archivo `.env.example` como `.env` y defina sus parámetros locales:
   ```ini
   DATABASE_URL=postgresql://postgres:tu_contraseña@localhost:5432/gestion
   SECRET_KEY=clave_aleatoria_segura
   ```

4. Ejecutar el esquema y cargar alimentos:
   ```bash
   psql -U postgres -d gestion -f schema.sql
   python cargar_alimentos.py
   ```

5. Iniciar la aplicación:
   - En Windows: Ejecutar `iniciar.bat` o:
     ```bash
     python app.py
     ```
   - Acceder en el navegador a `http://127.0.0.1:5000`

---

## 6. Despliegue en la Nube (Supabase + Render)

### Paso 1: Configurar Base de Datos en Supabase
1. Cree un proyecto nuevo en [Supabase](https://supabase.com).
2. Diríjase a **SQL Editor**, pegue el contenido de `schema.sql` y ejecútelo.
3. Diríjase a **Project Settings -> Database -> Connection string (URI)** y copie la URL de conexión PostgreSQL (modo Transaction o Session Pooler, puerto 6543 o 5432).
4. Opcionalmente, ejecute `cargar_alimentos.py` con esa URL en `DATABASE_URL` para poblar el catálogo de alimentos.

### Paso 2: Desplegar el Servicio en Render
1. En [Render](https://render.com), seleccione **New + -> Web Service** y conecte este repositorio.
2. Configure los parámetros:
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: Free
3. En **Environment Variables**, agregue:
   - `DATABASE_URL`: Pegue la URL de conexión obtenida de Supabase.
   - `SECRET_KEY`: Ingrese una cadena de caracteres aleatoria.
4. Pulse **Deploy**. La aplicación quedará publicada con certificado SSL automático (HTTPS).

---

## 7. Instalación en Dispositivos Móviles (PWA)

Con la aplicación desplegada bajo protocolo HTTPS:

- **Android (Chrome / Edge / Brave)**:
  1. Ingrese a la URL asignada.
  2. Pulse el botón **Instalar app** en la barra superior o seleccione **Instalar aplicación** en el menú del navegador.
- **iOS (Safari)**:
  1. Ingrese a la URL en Safari.
  2. Pulse el botón **Compartir** (icono de cuadrado con flecha).
  3. Seleccione **Agregar a pantalla de inicio**.

---

## 8. Seguridad y Privacidad

- El archivo `.gitignore` previene la publicación de credenciales locales (`.env`) o claves de sesión (`.secret_key`).
- El código fuente no contiene credenciales hardcodeadas; la configuración se resuelve dinámicamente mediante variables de entorno.
- Las consultas SQL implementan parametrización en todas las operaciones, neutralizando vectores de inyección SQL.
- El aislamiento de datos por usuario se garantiza en todas las tablas mediante validación obligatoria del identificador de sesión.

---

## Licencia

Distribuido bajo la Licencia MIT. Consulte el archivo `LICENSE` para más información.
