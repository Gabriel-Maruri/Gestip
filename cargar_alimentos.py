"""Carga alimentos desde alimentos.csv a la tabla alimento de PostgreSQL."""
import csv
import os
import psycopg2
from psycopg2.extras import execute_values
from app import DSN

def main():
    ruta_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alimentos.csv")
    if not os.path.exists(ruta_csv):
        print("No se encontró alimentos.csv")
        return

    con = psycopg2.connect(DSN)
    with con, con.cursor() as cur:
        with open(ruta_csv, encoding="utf-8-sig") as f:
            lector = csv.DictReader(f)
            filas = []
            for r in lector:
                filas.append((
                    r["nombre"].strip(),
                    r.get("categoria", "Otros").strip(),
                    r.get("unidad", "unidad").strip(),
                    float(r.get("gramos_unidad", 100)),
                    float(r["kcal"]),
                    float(r["proteina"]),
                    float(r.get("carbohidratos", 0)),
                    float(r.get("grasa", 0)),
                    float(r.get("fibra", 0)),
                    float(r.get("calcio_mg", 0)),
                    float(r.get("hierro_mg", 0)),
                    float(r.get("potasio_mg", 0)),
                    float(r.get("vit_c_mg", 0)),
                    float(r.get("vit_a_ug", 0)),
                    float(r.get("vit_b12_ug", 0))
                ))

        cols = "nombre, categoria, unidad, gramos_unidad, kcal, proteina, carbohidratos, grasa, fibra, calcio_mg, hierro_mg, potasio_mg, vit_c_mg, vit_a_ug, vit_b12_ug"
        execute_values(cur, f"""
            INSERT INTO alimento ({cols}) VALUES %s
            ON CONFLICT (nombre) DO UPDATE SET
                categoria = EXCLUDED.categoria,
                unidad = EXCLUDED.unidad,
                gramos_unidad = EXCLUDED.gramos_unidad,
                kcal = EXCLUDED.kcal,
                proteina = EXCLUDED.proteina,
                carbohidratos = EXCLUDED.carbohidratos,
                grasa = EXCLUDED.grasa,
                fibra = EXCLUDED.fibra,
                calcio_mg = EXCLUDED.calcio_mg,
                hierro_mg = EXCLUDED.hierro_mg,
                potasio_mg = EXCLUDED.potasio_mg,
                vit_c_mg = EXCLUDED.vit_c_mg,
                vit_a_ug = EXCLUDED.vit_a_ug,
                vit_b12_ug = EXCLUDED.vit_b12_ug
        """, filas)
        print(f"Éxito: {len(filas)} alimentos cargados/actualizados en PostgreSQL.")

    con.close()

if __name__ == "__main__":
    main()
