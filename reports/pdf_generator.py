import os
import sys
import io
from typing import Dict, Any, List

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class PDFReportGenerator:
    """
    Gerador de Relatórios Executivos para a Vanguard Data.
    Gera relatórios nos formatos HTML e PDF nos idiomas PT-BR, PT-PT e ES.
    """

    TRANSLATIONS = {
        "PT-BR": {
            "title": "RELATÓRIO EXECUTIVO DE INTELIGÊNCIA DE ATIVOS",
            "subtitle": "Vanguard Data - Análise Multidimensional",
            "asset_id": "ID do Ativo",
            "canonical_name": "Nome Canônico",
            "sci_traction": "Tração Científica",
            "ind_traction": "Tração Industrial",
            "supply_risk": "Risco de Oferta",
            "confidence": "Confiança do Sinal",
            "recommendations_title": "Recomendações Estratégicas (Síntese via IA)",
            "col_asset": "Ativo",
            "col_rd": "Inovação & P&D",
            "col_procurement": "Compras & Procurement",
            "footer": "Relatório gerado automaticamente pela Plataforma Vanguard Data (Brasil)"
        },
        "PT-PT": {
            "title": "RELATÓRIO EXECUTIVO DE INTELIGÊNCIA DE ATIVOS",
            "subtitle": "Vanguard Data - Análise Multidimensional",
            "asset_id": "ID do Ativo",
            "canonical_name": "Nome Canónico",
            "sci_traction": "Tracção Científica",
            "ind_traction": "Tracção Industrial",
            "supply_risk": "Risco de Oferta",
            "confidence": "Confiança do Sinal",
            "recommendations_title": "Recomendações Estratégicas (Síntese via IA)",
            "col_asset": "Ativo",
            "col_rd": "Inovação & I&D",
            "col_procurement": "Compras & Procurement",
            "footer": "Relatório gerado automaticamente pela Plataforma Vanguard Data (Portugal)"
        },
        "ES": {
            "title": "INFORME EJECUTIVO DE INTELIGENCIA DE ACTIVOS",
            "subtitle": "Vanguard Data - Análisis Multidimensional",
            "asset_id": "ID del Activo",
            "canonical_name": "Nombre Canónico",
            "sci_traction": "Tracción Científica",
            "ind_traction": "Tracción Industrial",
            "supply_risk": "Riesgo de Oferta",
            "confidence": "Confianza de la Señal",
            "recommendations_title": "Recomendaciones Estratégicas (Síntesis vía IA)",
            "col_asset": "Activo",
            "col_rd": "Innovación & I+D",
            "col_procurement": "Compras & Procurement",
            "footer": "Informe generado automáticamente por la Plataforma Vanguard Data"
        }
    }

    def __init__(self, output_dir: str = "reports/output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_report(self, evaluations: List[Dict[str, Any]], lang: str = "PT-BR") -> str:
        """Gera o HTML e converte diretamente para arquivo PDF."""
        t = self.TRANSLATIONS.get(lang.upper(), self.TRANSLATIONS["PT-BR"])

        rows_html = ""
        for item in evaluations:
            rows_html += f"""
            <tr>
                <td><strong>{item['asset_id']}</strong></td>
                <td>{item['canonical_name']}</td>
                <td style="text-align: center;">{item['scientific_traction']}</td>
                <td style="text-align: center;">{item['industrial_traction']}</td>
                <td style="text-align: center;"><span class="badge {item['supply_risk'].lower().replace(' ', '-')}">{item['supply_risk']}</span></td>
                <td style="text-align: center;"><span class="badge conf-{item['confidence_level'].lower()}">{item['confidence_level']}</span></td>
            </tr>
            """

        recommendation_rows_html = ""
        for item in evaluations:
            inovacao_pd = item.get("inovacao_pd")
            compras_procurement = item.get("compras_procurement")
            if not inovacao_pd and not compras_procurement:
                continue
            recommendation_rows_html += f"""
            <tr>
                <td class="col-ativo">{item['canonical_name']}</td>
                <td>{inovacao_pd or '—'}</td>
                <td>{compras_procurement or '—'}</td>
            </tr>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="{lang.lower()}">
<head>
    <meta charset="UTF-8">
    <title>{t['title']}</title>
    <style>
        @page {{
            size: A4;
            margin: 15mm 12mm;
        }}
        body {{
            font-family: 'Helvetica Neue', Arial, sans-serif;
            color: #2c3e50;
            margin: 0;
            padding: 0;
            background-color: #ffffff;
        }}
        .header {{
            border-bottom: 3px solid #1a365d;
            padding-bottom: 12px;
            margin-bottom: 25px;
        }}
        .header h1 {{
            color: #1a365d;
            font-size: 16pt;
            margin: 0 0 6px 0;
            text-transform: uppercase;
        }}
        .header h2 {{
            color: #718096;
            font-size: 10pt;
            margin: 0;
            font-weight: normal;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        th {{
            background-color: #2b6cb0;
            color: #ffffff;
            font-size: 8.5pt;
            text-transform: uppercase;
            padding: 8px 6px;
            text-align: left;
        }}
        td {{
            padding: 8px 6px;
            font-size: 9pt;
            border-bottom: 1px solid #e2e8f0;
        }}
        tr:nth-child(even) {{
            background-color: #f7fafc;
        }}
        .badge {{
            padding: 3px 6px;
            border-radius: 4px;
            font-size: 7.5pt;
            font-weight: bold;
        }}
        .baixo-risco {{ background-color: #c6f6d5; color: #22543d; }}
        .medio-risco {{ background-color: #feebc8; color: #744210; }}
        .alto-risco {{ background-color: #fed7d7; color: #742a2a; }}
        .conf-alta {{ color: #2b6cb0; font-weight: bold; }}
        .conf-média, .conf-media {{ color: #d69e2e; font-weight: bold; }}
        .conf-baixa {{ color: #e53e3e; font-weight: bold; }}
        .section-title {{
            color: #1a365d;
            font-size: 12pt;
            margin: 30px 0 4px 0;
            text-transform: uppercase;
        }}
        table.recommendations {{
            table-layout: fixed;
        }}
        table.recommendations th.header-pd {{
            background-color: #1b4d3e;
            color: #ffffff;
        }}
        table.recommendations th.header-procurement {{
            background-color: #b38646;
            color: #ffffff;
        }}
        table.recommendations td {{
            vertical-align: top;
            line-height: 1.4;
            font-size: 8.5pt;
        }}
        table.recommendations tr:nth-child(even) td {{
            background-color: #f7fafc;
        }}
        table.recommendations td.col-ativo {{
            background-color: #1b4d3e !important;
            color: #ffffff !important;
            font-weight: bold;
            width: 18%;
        }}
        .footer {{
            margin-top: 30px;
            border-top: 1px solid #e2e8f0;
            padding-top: 10px;
            font-size: 8pt;
            color: #a0aec0;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{t['title']}</h1>
        <h2>{t['subtitle']}</h2>
    </div>
    <table>
        <thead>
            <tr>
                <th>{t['asset_id']}</th>
                <th>{t['canonical_name']}</th>
                <th style="text-align: center;">{t['sci_traction']}</th>
                <th style="text-align: center;">{t['ind_traction']}</th>
                <th style="text-align: center;">{t['supply_risk']}</th>
                <th style="text-align: center;">{t['confidence']}</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    {f'''<h3 class="section-title">{t['recommendations_title']}</h3>
    <table class="recommendations">
        <thead>
            <tr>
                <th>{t['col_asset']}</th>
                <th class="header-pd">{t['col_rd']}</th>
                <th class="header-procurement">{t['col_procurement']}</th>
            </tr>
        </thead>
        <tbody>
            {recommendation_rows_html}
        </tbody>
    </table>''' if recommendation_rows_html else ''}
    <div class="footer">
        {t['footer']}
    </div>
</body>
</html>"""

        prefix = lang.lower().replace('-', '_')
        html_path = os.path.join(self.output_dir, f"relatorio_vanguard_{prefix}.html")
        pdf_path = os.path.join(self.output_dir, f"relatorio_vanguard_{prefix}.pdf")

        # Salva o arquivo HTML
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # Converte para PDF
        try:
            from weasyprint import HTML
            HTML(filename=html_path).write_pdf(pdf_path)
            print(f"   [+] PDF gerado com sucesso via WeasyPrint: {pdf_path}")
        except Exception:
            try:
                from xhtml2pdf import pisa
                with open(pdf_path, "wb") as pdf_file:
                    pisa.CreatePDF(html_content, dest=pdf_file)
                print(f"   [+] PDF gerado com sucesso via xhtml2pdf: {pdf_path}")
            except Exception as e:
                print(f"   [-] Falha ao gerar PDF automaticamente ({e}). HTML mantido em: {html_path}")
                return html_path

        return pdf_path

    def export_all_languages(self, evaluations: List[Dict[str, Any]]) -> List[str]:
        """Gera os relatórios PDF para PT-BR, PT-PT e ES."""
        generated_files = []
        for lang in ["PT-BR", "PT-PT", "ES"]:
            file_path = self.generate_report(evaluations, lang=lang)
            generated_files.append(file_path)
        return generated_files


if __name__ == "__main__":
    generator = PDFReportGenerator()
    mock_evals = [
        {
            "asset_id": "AT-001", "canonical_name": "Bakuchiol", "scientific_traction": "4.0/10",
            "industrial_traction": "4.0/10", "supply_risk": "BAIXO RISCO", "confidence_level": "ALTA",
            "inovacao_pd": "Investir em estudos de estabilidade e eficácia comparativa frente ao retinol.",
            "compras_procurement": "Negociar contratos de médio prazo com fornecedores já qualificados."
        },
        {
            "asset_id": "AT-002", "canonical_name": "Centella Asiática", "scientific_traction": "4.0/10",
            "industrial_traction": "0.0/10", "supply_risk": "BAIXO RISCO", "confidence_level": "BAIXA",
            "inovacao_pd": "Ampliar coleta de evidências científicas antes de priorizar novas formulações.",
            "compras_procurement": "Manter fornecedor atual; volume de compra ainda não justifica diversificação."
        }
    ]
    generator.export_all_languages(mock_evals)
