# CONTEXTO DEL PROYECTO: GESTIP

Documento maestro de arquitectura, estructura, modelo de datos y reglas de negocio para agentes y desarrolladores.

---

## 1. Visión General del Proyecto
**Gestip** es una aplicación web integral y minimalista para la gestión personal en tres pilares fundamentales:
1. **Finanzas**: Control de ingresos, egresos, gastos fijos mensuales y gráficos interactivos de balance y distribución por categoría.
2. **Alimentación**: Registro y seguimiento calórico y proteico diario, catálogo de alimentos (base de datos BEDCA depurada + creación rápida de alimentos con nombre, calorías y proteínas), registro de comidas diarias y ensamblado de platos/recetas combinadas.
3. **Entrenamiento**: Registro de rutinas de fuerza/hipertrofia mediante plantillas, asignación de días de la semana (desplegable e intuitivo), registro de series en vivo (peso, repeticiones, RIR/fallo), finalización de rutina con resguardo en historial y cálculo automático de récords personales (PRs).
4. **Panel General (Dashboard)**: Vista unificada al iniciar sesión que consolida los indicadores clave del día y la semana (balance calórico, tonelaje levantado, balance financiero y actividad reciente).

---

## 2. Stack Tecnológico y Despliegue
- **Backend**: Python 3.11+, Flask.
- **Base de Datos**: PostgreSQL (Supabase en producción con Pooler en modo *Session* por puerto 5432; PostgreSQL local para pruebas).
- **Driver DB**: `psycopg2-binary`.
- **Frontend**: Plantillas Jinja2 (`templates/`), HTML5 semántico, CSS moderno con variables CSS (`var(--...)`), Vanilla JavaScript para reactividad ligera sin frameworks pesados.
- **Control de Versiones y CI/CD**:
  - Repositorio GitHub: `https://github.com/Gabriel-Maruri/Gestip.git` (rama `main`).
  - Hosting: **Render** (despliegue automático conectado al branch `main`).
  - Variables de entorno en producción: `DATABASE_URL` (Supabase Session Pooler), `SECRET_KEY`, `PORT` (usualmente 5000 / 10000 asignado por Render).

---

## 3. Principios de Diseño e Interfaz
- **Sin emojis**: Estrictamente prohibido el uso de emojis en títulos, botones o textos informativos. Se utilizan iconos SVG minimalistas o etiquetas de texto sobrias.
- **Mobile-First y Diseño Lineal**: Todas las vistas están optimizadas para pantallas táctiles estrechas (360px - 480px) y se adaptan a escritorio mediante contenedores fluidos sin desbordamientos horizontales.
- **Paleta de Colores y Tokens**:
  - Fondo oscuro / neutral: `--bg-base: #0f172a`, `--bg-surface: #1e293b`, `--bg-subtle: #334155`.
  - Bordes: `--border-light: #475569`, `--border-divider: #334155`.
  - Acentos: Primario `--accent-primary: #3b82f6` (azul), Éxito/Finalizar `#059669` (esmeralda), Alerta/Eliminar `#ef4444` (rojo).

---

## 4. Estructura del Código

```text
c:\gestion\
│
├── app.py                      # Controlador principal, rutas, autenticación y lógica de negocio
├── schema.sql                  # Definición DDL de tablas e índices PostgreSQL
├── requirements.txt            # Dependencias del proyecto (Flask, psycopg2-binary, etc.)
├── .env                        # Variables de entorno locales (DATABASE_URL, SECRET_KEY)
├── .gitignore                  # Exclusiones de Git (.env, .agents/, graphify-out/, etc.)
├── CONTEXTO_PROYECTO.md        # Este documento maestro de contexto
│
├── static/
│   ├── css/
│   │   └── style.css           # Estilos globales, variables de color y responsive
│   └── js/                     # Scripts auxiliares para gráficos interactivos
│
└── templates/
    ├── base.html               # Shell base: navbar superior, navegación móvil inferior, auth badge
    ├── index.html              # Panel General (Dashboard unificado día/semana)
    ├── login.html              # Inicio de sesión
    ├── registro.html           # Registro de nuevos usuarios
    ├── finanzas.html           # Módulo de finanzas (ingresos, egresos, gastos fijos, gráfico SVG)
    ├── alimentacion.html       # Módulo de nutrición (catálogo BEDCA, agregar alimento, registrar consumo, platos)
    └── entrenamiento.html      # Módulo de entrenamiento (plantillas, días desplegables, series, finalizar rutina, PRs)
```

---

## 5. Modelo de Datos y Esquema de Base de Datos

Todas las consultas deben estar aisladas por usuario (`user_id = %s`).

### 5.1 Autenticación y Usuarios
- **`usuarios`**: `id`, `nombre`, `email` (único), `password_hash`, `creado_en`.

### 5.2 Módulo de Finanzas
- **`ingresos`**: `id`, `user_id`, `monto`, `categoria`, `descripcion`, `fecha`.
- **`gastos`**: `id`, `user_id`, `monto`, `categoria`, `descripcion`, `fecha`.
- **`gastos_fijos`**: `id`, `user_id`, `monto`, `categoria`, `descripcion`, `dia_pago`, `activo`.

### 5.3 Módulo de Alimentación
- **`alimentos`**: `id`, `nombre`, `calorias` (por 100g), `proteinas`, `carbohidratos`, `grasas`, `user_id` (NULL si es alimento global de BEDCA, o ID de usuario si es personalizado).
- **`comidas`**: `id`, `user_id`, `alimento_id`, `cantidad_gramos`, `tipo_comida` (Desayuno, Almuerzo, Cena, Snack), `fecha`.
- **`platos` / `plato_ingredientes`**: Permite al usuario agrupar varios alimentos en un único plato/receta calculando la sumatoria de macronutrientes.

### 5.4 Módulo de Entrenamiento
- **`rutinas`**: `id`, `user_id`, `nombre` (plantilla), `descripcion`, `dias_semana` (ej: "Lunes, Miércoles, Viernes").
- **`ejercicios`**: `id`, `rutina_id`, `user_id`, `nombre`, `series_objetivo`, `repeticiones_objetivo`.
- **`registro_serie`**: `id`, `user_id`, `ejercicio_id`, `serie_numero`, `peso`, `repeticiones`, `rir`, `fecha`, `finalizada` (BOOLEAN DEFAULT FALSE).
  - *Mecánica*: Mientras `finalizada = FALSE` y `fecha = CURRENT_DATE`, la serie aparece como activa en la plantilla de hoy. Al pulsar "Finalizar rutina", se marca `finalizada = TRUE`, limpiando la plantilla y pasando automáticamente al historial y récords personales (PRs).
- **`entrenamiento`**: `id`, `user_id`, `fecha`, `notas`.

---

## 6. Endpoints y Rutas Principales

| Ruta | Métodos | Descripción |
|---|---|---|
| `/` | GET | Panel general con métricas diarias y semanales consolidadas |
| `/login` | GET, POST | Autenticación con sesión segura (`session['user_id']`) |
| `/registro` | GET, POST | Registro con hash de contraseña (`werkzeug.security`) |
| `/logout` | GET | Cierre de sesión |
| `/finanzas` | GET | Tablero financiero, balances e historial |
| `/finanzas/ingreso` | POST | Registrar nuevo ingreso |
| `/finanzas/gasto` | POST | Registrar nuevo gasto |
| `/finanzas/fijo` | POST | Configurar transacción fija recurrente |
| `/alimentacion` | GET | Tablero nutricional diario y catálogo de alimentos |
| `/alimentacion/alimento/nuevo` | POST | Crear alimento rápido (nombre, calorías, proteínas) |
| `/alimentacion/consumo` | POST | Registrar ingesta de comida con gramaje |
| `/alimentacion/plato/nuevo` | POST | Crear plato combinado sumando ingredientes |
| `/entrenamiento` | GET | Panel de entrenamiento, plantillas y series activas |
| `/entrenamiento/rutina/nueva` | POST | Crear nueva plantilla de entrenamiento |
| `/entrenamiento/dia/<id>/dias` | POST | Asignar días de la semana a la plantilla (desplegable) |
| `/entrenamiento/ejercicio/nuevo` | POST | Agregar ejercicio a la plantilla |
| `/entrenamiento/serie` | POST | Registrar serie (peso, repeticiones, rir) |
| `/entrenamiento/finalizar` | POST | Finalizar rutina de hoy (marca series como finalizadas) |
| `/entrenamiento/limpiar-hoy` | POST | Descartar series de hoy sin registrar en historial |

---

## 7. Directrices Críticas para Nuevas Tareas
1. **Verificación de conexión**: La base de datos en Supabase usa el connection pooler en modo Session (`port: 5432`). Nunca alterar el puerto a transaction (6543) sin soporte de `PREPARE`/`DEALLOCATE`.
2. **Seguridad multi-inquilino**: Toda sentencia `SELECT`, `UPDATE`, `DELETE` o `INSERT` debe incluir explícitamente `user_id = %s`.
3. **No ejecutar comandos lentos innecesarios**: Para ediciones normales, modificar archivos directamente sin invocar subagentes pesados salvo petición explícita del usuario.
4. **Git workflow**: Todo cambio probado se sube con `git add`, `git commit -m "..."` y `git push origin main`.
