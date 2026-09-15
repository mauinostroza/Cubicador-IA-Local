#!/usr/bin/env python3
"""Crea un PDF de prueba con tablas técnicas para probar el extractor."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

from pathlib import Path

OUTPUT = str(Path(__file__).resolve().parent / "sample-test.pdf")

doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    leftMargin=2 * cm,
    rightMargin=2 * cm,
    topMargin=2 * cm,
    bottomMargin=2 * cm,
)

styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    'CustomTitle',
    parent=styles['Title'],
    fontSize=18,
    spaceAfter=20,
    alignment=TA_CENTER,
)
heading_style = ParagraphStyle(
    'CustomHeading',
    parent=styles['Heading2'],
    fontSize=14,
    spaceBefore=14,
    spaceAfter=10,
    textColor=colors.HexColor('#0f766e'),
)
body_style = ParagraphStyle(
    'CustomBody',
    parent=styles['Normal'],
    fontSize=10,
    leading=14,
    alignment=TA_JUSTIFY,
    spaceAfter=8,
)

elements = []

elements.append(Paragraph("Informe Técnico de Materiales de Construcción", title_style))
elements.append(Paragraph(
    "Este documento presenta los resultados de ensayos de laboratorio realizados "
    "sobre muestras de hormigón y acero estructural, conforme a las normas ASTM y ACI.",
    body_style
))
elements.append(Spacer(1, 0.5 * cm))

elements.append(Paragraph("1. Introducción", heading_style))
elements.append(Paragraph(
    "El presente informe resume las propiedades mecánicas y físicas de los materiales "
    "evaluados. Los ensayos se realizaron en el Laboratorio de Materiales de la "
    "Facultad de Ingeniería durante el segundo semestre del año 2025. Se analizaron "
    "tres tipos de mezclas de hormigón y dos calidades de acero de refuerzo.",
    body_style
))

elements.append(Paragraph("2. Resultados de Ensayos", heading_style))
elements.append(Paragraph(
    "La Tabla 4.1 muestra la composición de las mezclas evaluadas, mientras que la "
    "Tabla 4.2 contiene las propiedades mecánicas obtenidas tras 28 días de curado.",
    body_style
))

elements.append(Paragraph("Tabla 4.1 - Composición de Mezclas de Hormigón", heading_style))
tabla_41_data = [
    ['Mezcla', 'Cemento (kg/m³)', 'Agua (L)', 'Arena (kg/m³)', 'Grava (kg/m³)'],
    ['M1', '350', '175', '720', '1080'],
    ['M2', '400', '180', '680', '1100'],
    ['M3', '450', '170', '650', '1120'],
]
tabla_41 = Table(tabla_41_data, colWidths=[2.5 * cm, 3.5 * cm, 2.5 * cm, 3.5 * cm, 3.5 * cm])
tabla_41.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0fdfa')]),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
]))
elements.append(tabla_41)
elements.append(Spacer(1, 0.6 * cm))

elements.append(Paragraph("Tabla 4.2 - Propiedades Mecánicas", heading_style))
elements.append(Paragraph(
    "Resultados de los ensayos mecánicos realizados a los 28 días de curado para cada mezcla.",
    body_style
))

tabla_42_data = [
    ['Propiedad', 'Unidad', 'M1', 'M2', 'M3'],
    ['Módulo de elasticidad', 'GPa', '28.5', '32.1', '35.8'],
    ['Resistencia a la compresión', 'MPa', '25.4', '31.2', '38.7'],
    ['Tensión admisible', 'MPa', '8.5', '10.5', '13.0'],
    ['Densidad', 'kg/m³', '2380', '2410', '2445'],
    ['Resistencia a la tracción', 'MPa', '2.8', '3.4', '4.1'],
    ['Conductividad térmica', 'W/m·K', '1.74', '1.78', '1.82'],
    ['Porosidad', '%', '14.2', '12.8', '11.3'],
]
tabla_42 = Table(tabla_42_data, colWidths=[5 * cm, 2.5 * cm, 2 * cm, 2 * cm, 2 * cm])
tabla_42.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0fdfa')]),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('ALIGN', (0, 1), (0, -1), 'LEFT'),
]))
elements.append(tabla_42)

elements.append(Spacer(1, 0.8 * cm))
elements.append(Paragraph(
    "Notas: Los valores presentados corresponden al promedio de tres probetas "
    "ensayadas bajo condiciones controladas de temperatura (20 ± 2 °C) y humedad "
    "relativa (95 ± 5 %). El módulo de elasticidad fue determinado mediante ensayo "
    "de compresión axial conforme a la norma ASTM C469.",
    body_style
))

elements.append(PageBreak())
elements.append(Paragraph("3. Análisis de Resultados", heading_style))
elements.append(Paragraph(
    "Los resultados obtenidos demuestran que la mezcla M3 presenta las mejores "
    "propiedades mecánicas, con una resistencia a la compresión 52 % superior a "
    "la mezcla M1. Sin embargo, el costo de la mezcla M3 es aproximadamente 22 % "
    "mayor debido al mayor contenido de cemento.",
    body_style
))

elements.append(Paragraph("Tabla 4.3 - Propiedades del Acero de Refuerzo", heading_style))
tabla_43_data = [
    ['Propiedad', 'Unidad', 'Acero A615', 'Acero A706'],
    ['Límite de fluencia', 'MPa', '420', '420'],
    ['Resistencia última', 'MPa', '620', '550'],
    ['Alargamiento', '%', '9', '14'],
    ['Módulo de elasticidad', 'GPa', '200', '200'],
]
tabla_43 = Table(tabla_43_data, colWidths=[5 * cm, 2.5 * cm, 3 * cm, 3 * cm])
tabla_43.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0fdfa')]),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('ALIGN', (0, 1), (0, -1), 'LEFT'),
]))
elements.append(tabla_43)

elements.append(Spacer(1, 0.5 * cm))
elements.append(Paragraph(
    "Conclusiones: Se recomienda el uso de la mezcla M2 para elementos estructurales "
    "de moderada solicitación, y la mezcla M3 para elementos críticos como columnas "
    "y muros de cortante. Para el acero de refuerzo, el A706 es preferible en zonas "
    "sísmicas debido a su mayor ductilidad.",
    body_style
))

doc.build(elements)
print(f"PDF de prueba creado en: {OUTPUT}")
