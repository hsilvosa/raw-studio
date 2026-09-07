#!/usr/bin/env python
"""CLI tool to run photographic model decision evaluation on library scenes."""
import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio.evaluator import run_evaluation
from studio.model import status as model_status


def main():
    parser = argparse.ArgumentParser(description='Evaluar decisiones de adaptación y modelo local en escenas de la biblioteca')
    parser.add_argument('--profile', default='09_PORTRA_WARM', help='ID del perfil de color a probar (default: 09_PORTRA_WARM)')
    parser.add_argument('--limit', type=int, default=3, help='Número máximo de escenas a evaluar (default: 3)')
    args = parser.parse_args()

    print("=================================================================")
    print("  EVALUACIÓN DE ADAPTACIÓN FOTOGRÁFICA Y MODELO QWEN3-VL")
    print("=================================================================")

    st = model_status()
    print(f"Estado del modelo local: {'Conectado (' + str(st.get('name')) + ')' if st.get('available') else 'No disponible (' + str(st.get('detail')) + ')'}")
    print(f"Perfil seleccionado: {args.profile}")
    print("Iniciando pruebas comparativas en escenas...")

    evaluations, report_path = run_evaluation(profile_id=args.profile)

    print(f"\nSe han evaluado {len(evaluations)} escenas con éxito.")
    print("-----------------------------------------------------------------")
    for ev in evaluations:
        print(f"\n[Foto: {ev['image_name']} ({ev['image_id']})]")
        t = ev['telemetry']
        print(f"  • Telemetría: Med={t['median']:.2f}, Piel={'Sí (' + str(round(t['skin_fraction']*100, 1)) + '%)' if t['skin_detected'] else 'No'}, Ruido={t['noise_sigma']:.4f}")
        print(f"  • Perfil fijo:       Calidad={ev['fixed']['metrics']['quality_score']} | Quemados={ev['fixed']['metrics']['white_clip']*100:.2f}% | Rango EV={ev['fixed']['metrics']['dr_ev']:.2f}")
        print(f"  • Adaptación escena: Calidad={ev['adapted']['metrics']['quality_score']} | Quemados={ev['adapted']['metrics']['white_clip']*100:.2f}% | Rango EV={ev['adapted']['metrics']['dr_ev']:.2f}")
        print(f"    Razón: {ev['adapted']['reason']}")
        if ev['model'].get('name'):
            print(f"  • Modelo Qwen3-VL:   Calidad={ev['model']['metrics']['quality_score']} | Quemados={ev['model']['metrics']['white_clip']*100:.2f}% | Rango EV={ev['model']['metrics']['dr_ev']:.2f}")
            print(f"    Criterio: {ev['model']['reason']}")

    print("\n-----------------------------------------------------------------")
    print(f"Reporte visual HTML generado en: {report_path.resolve()}")
    print("=================================================================")


if __name__ == '__main__':
    main()
