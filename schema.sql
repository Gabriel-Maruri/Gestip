-- Esquema completo multiusuario para la base de datos "gestion"

-- Extensiones recomendadas
CREATE EXTENSION IF NOT EXISTS unaccent;

-- 0. Usuarios y autenticación
CREATE TABLE IF NOT EXISTS usuario (
  id SERIAL PRIMARY KEY,
  nombre TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 1. Finanzas: Ingresos, Egresos, Presupuestos y Metas
CREATE TABLE IF NOT EXISTS fuente_ingreso (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  UNIQUE(user_id, nombre)
);

CREATE TABLE IF NOT EXISTS ingreso (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  fuente_id INT NOT NULL REFERENCES fuente_ingreso(id) ON DELETE CASCADE,
  monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
  fecha DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS categoria_egreso (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  presupuesto_mensual NUMERIC(12,2) DEFAULT 0,
  UNIQUE(user_id, nombre)
);

CREATE TABLE IF NOT EXISTS egreso (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  categoria_id INT NOT NULL REFERENCES categoria_egreso(id) ON DELETE CASCADE,
  concepto TEXT,
  monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
  fecha DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS meta_ahorro (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  monto_objetivo NUMERIC(12,2) NOT NULL CHECK (monto_objetivo > 0),
  monto_actual NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (monto_actual >= 0),
  fecha_limite DATE
);

CREATE TABLE IF NOT EXISTS transaccion_fija (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  tipo TEXT NOT NULL CHECK (tipo IN ('ingreso', 'egreso')),
  concepto TEXT NOT NULL,
  monto NUMERIC(12,2) NOT NULL CHECK (monto > 0),
  categoria_id INT REFERENCES categoria_egreso(id) ON DELETE SET NULL,
  fuente_id INT REFERENCES fuente_ingreso(id) ON DELETE SET NULL,
  dia_mes INT NOT NULL DEFAULT 1 CHECK (dia_mes BETWEEN 1 AND 31)
);

-- 2. Alimentación (Catálogo global compartido y registros por usuario)
CREATE TABLE IF NOT EXISTS alimento (
  id SERIAL PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,
  categoria TEXT,
  unidad TEXT DEFAULT 'unidad',
  gramos_unidad NUMERIC(7,1) DEFAULT 100,
  kcal NUMERIC(6,1) NOT NULL,
  proteina NUMERIC(5,1) NOT NULL,
  carbohidratos NUMERIC(5,1) NOT NULL DEFAULT 0,
  grasa NUMERIC(5,1) NOT NULL DEFAULT 0,
  fibra NUMERIC(5,1) NOT NULL DEFAULT 0,
  calcio_mg NUMERIC(6,1) NOT NULL DEFAULT 0,
  hierro_mg NUMERIC(5,1) NOT NULL DEFAULT 0,
  potasio_mg NUMERIC(6,1) NOT NULL DEFAULT 0,
  vit_c_mg NUMERIC(5,1) NOT NULL DEFAULT 0,
  vit_a_ug NUMERIC(6,1) NOT NULL DEFAULT 0,
  vit_b12_ug NUMERIC(5,2) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consumo (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  alimento_id INT NOT NULL REFERENCES alimento(id) ON DELETE CASCADE,
  gramos NUMERIC(7,1) NOT NULL CHECK (gramos > 0),
  porciones NUMERIC(6,2),
  momento TEXT NOT NULL DEFAULT 'Almuerzo',
  fecha DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS perfil_nutricional (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL UNIQUE REFERENCES usuario(id) ON DELETE CASCADE,
  meta_kcal NUMERIC(6,1) NOT NULL DEFAULT 2000,
  meta_proteina NUMERIC(5,1) NOT NULL DEFAULT 140,
  meta_carbos NUMERIC(5,1) NOT NULL DEFAULT 220,
  meta_grasa NUMERIC(5,1) NOT NULL DEFAULT 60,
  meta_agua_ml INT NOT NULL DEFAULT 2000
);

CREATE TABLE IF NOT EXISTS registro_agua (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  fecha DATE NOT NULL DEFAULT CURRENT_DATE,
  vasos INT NOT NULL DEFAULT 0 CHECK (vasos >= 0),
  UNIQUE (user_id, fecha)
);

CREATE TABLE IF NOT EXISTS receta (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  momento TEXT NOT NULL DEFAULT 'Desayuno'
);

CREATE TABLE IF NOT EXISTS receta_item (
  id SERIAL PRIMARY KEY,
  receta_id INT NOT NULL REFERENCES receta(id) ON DELETE CASCADE,
  alimento_id INT NOT NULL REFERENCES alimento(id) ON DELETE CASCADE,
  gramos NUMERIC(7,1) NOT NULL CHECK (gramos > 0),
  porciones NUMERIC(6,2)
);

-- Vista de resumen diario multiusuario
CREATE OR REPLACE VIEW resumen_diario AS
SELECT 
  c.user_id,
  c.fecha, 
  ROUND(COALESCE(SUM(a.kcal * c.gramos / 100), 0)::numeric, 1) AS kcal, 
  ROUND(COALESCE(SUM(a.proteina * c.gramos / 100), 0)::numeric, 1) AS proteina,
  ROUND(COALESCE(SUM(a.carbohidratos * c.gramos / 100), 0)::numeric, 1) AS carbohidratos,
  ROUND(COALESCE(SUM(a.grasa * c.gramos / 100), 0)::numeric, 1) AS grasa,
  ROUND(COALESCE(SUM((a.calcio_mg + a.hierro_mg + a.potasio_mg + a.vit_c_mg + a.vit_a_ug/1000.0 + a.vit_b12_ug/1000.0) * c.gramos / 100), 0)::numeric, 1) AS micros_mg
FROM consumo c 
JOIN alimento a ON a.id = c.alimento_id 
GROUP BY c.user_id, c.fecha;

-- 3. Entrenamiento
CREATE TABLE IF NOT EXISTS dia_rutina (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  dias_semana TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ejercicio (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  series INT NOT NULL CHECK (series > 0),
  repeticiones INT NOT NULL CHECK (repeticiones > 0),
  dia_id INT REFERENCES dia_rutina(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entrenamiento (
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  fecha DATE NOT NULL,
  PRIMARY KEY (user_id, fecha)
);

CREATE TABLE IF NOT EXISTS registro_serie (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
  ejercicio_id INT NOT NULL REFERENCES ejercicio(id) ON DELETE CASCADE,
  fecha DATE NOT NULL DEFAULT CURRENT_DATE,
  numero_serie INT NOT NULL CHECK (numero_serie > 0),
  peso_kg NUMERIC(6,2) NOT NULL DEFAULT 0 CHECK (peso_kg >= 0),
  repeticiones INT NOT NULL CHECK (repeticiones > 0),
  finalizada BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS catalogo_ejercicio (
  id SERIAL PRIMARY KEY,
  grupo TEXT NOT NULL,
  nombre TEXT NOT NULL,
  series INT NOT NULL,
  repeticiones INT NOT NULL
);

-- ============================================================================
-- Índices para optimización de alto rendimiento en consultas multiusuario
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_consumo_user_fecha ON consumo(user_id, fecha);
CREATE INDEX IF NOT EXISTS idx_consumo_alimento ON consumo(alimento_id);
CREATE INDEX IF NOT EXISTS idx_ingreso_user_fecha ON ingreso(user_id, fecha);
CREATE INDEX IF NOT EXISTS idx_egreso_user_fecha ON egreso(user_id, fecha);
CREATE INDEX IF NOT EXISTS idx_egreso_categoria ON egreso(categoria_id);
CREATE INDEX IF NOT EXISTS idx_meta_ahorro_user ON meta_ahorro(user_id);
CREATE INDEX IF NOT EXISTS idx_transaccion_fija_user ON transaccion_fija(user_id);
CREATE INDEX IF NOT EXISTS idx_receta_user ON receta(user_id);
CREATE INDEX IF NOT EXISTS idx_receta_item_receta ON receta_item(receta_id);
CREATE INDEX IF NOT EXISTS idx_dia_rutina_user ON dia_rutina(user_id);
CREATE INDEX IF NOT EXISTS idx_ejercicio_user ON ejercicio(user_id);
CREATE INDEX IF NOT EXISTS idx_registro_serie_user_fecha ON registro_serie(user_id, fecha);
CREATE INDEX IF NOT EXISTS idx_registro_serie_ejercicio ON registro_serie(ejercicio_id, user_id);
CREATE INDEX IF NOT EXISTS idx_catalogo_grupo ON catalogo_ejercicio(grupo);

