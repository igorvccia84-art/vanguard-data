import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.entity_resolver import EntityResolver
from connectors.pubmed import PubMedConnector
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector
from core.score_engine import ScoreEngine
from core.database import DatabaseManager
from reports.pdf_generator import PDFReportGenerator


def main():
    print("=" * 70)
    print("       PLATAFORMA VANGUARD DATA - PIPELINE DE INTELIGÊNCIA")
    print("=" * 70)

    # 1. Carregar Taxonomia de Ativos
    taxonomy_file = "data/taxonomy/ativos_mvp.json"
    resolver = EntityResolver(taxonomy_path=taxonomy_file)
    assets = resolver.assets
    print(f"\n[1] Taxonomia carregada: {len(assets)} ativo(s) configurado(s).")

    # 2. Inicializar Conectores, Engine de Score, Banco e Relatórios
    pubmed_conn = PubMedConnector(resolver=resolver)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)

    score_engine = ScoreEngine()
    db_manager = DatabaseManager()
    pdf_generator = PDFReportGenerator()

    evaluations_for_report = []

    # 3. Processar cada Ativo
    for asset in assets:
        asset_id = asset["asset_id"]
        canonical_name = asset["canonical_name"]
        search_query = asset.get("botanical_name") or asset.get("aliases", [canonical_name])[0]

        print("\n" + "-" * 50)
        print(f"🔍 Processando Ativo: {canonical_name} ({asset_id})")
        print("-" * 50)

        # Coleta Científica (PubMed)
        pmids = pubmed_conn.search_articles(search_query, max_results=2)
        pubmed_results = [pubmed_conn.fetch_article_details(p) for p in pmids]

        # Coleta de Patentes
        raw_patents = patent_conn.fetch_patents_mock(search_query)
        patent_results = [patent_conn.process_patent(p) for p in raw_patents]

        # Coleta Regulatória & Comex
        reg_data = comex_conn.fetch_regulatory_status(asset_id)
        hs_code = asset.get("hs_codes", [None])[0]
        trade_data = comex_conn.fetch_import_volume_mock(hs_code)

        total_evidences = len(pubmed_results) + len(patent_results)
        print(f"   ➜ Total de evidências coletadas: {total_evidences}")

        # Avaliação e cálculo de scores
        assessment = score_engine.generate_assessment(
            pubmed_data=pubmed_results,
            patent_data=patent_results,
            reg_data=reg_data,
            trade_data=trade_data
        )
        print(f"   ➜ Tração Científica: {assessment['tracao_cientifica']}")
        print(f"   ➜ Tração Industrial: {assessment['tracao_industrial']}")
        print(f"   ➜ Risco de Oferta:   {assessment['risco_oferta']}")
        print(f"   ➜ Confiança Sinal:  {assessment['confianca_sinal']}")

        # Guardar no Banco SQLite
        db_manager.save_evaluation(asset_id, canonical_name, assessment)
        print("   💾 Avaliação salva no banco de dados com sucesso.")

        # Estrutura para os relatórios
        evaluations_for_report.append({
            "asset_id": asset_id,
            "canonical_name": canonical_name,
            "scientific_traction": assessment["tracao_cientifica"],
            "industrial_traction": assessment["tracao_industrial"],
            "supply_risk": assessment["risco_oferta"],
            "confidence_level": assessment["confianca_sinal"]
        })

    # 4. Gerar Relatórios Executivos em PDF
    print("\n" + "=" * 70)
    print("📄 GERANDO RELATÓRIOS EXECUTIVOS EM PDF (PT-BR, PT-PT, ES)")
    print("=" * 70)
    pdf_generator.export_all_languages(evaluations_for_report)

    print("\n✅ Execução do Pipeline Concluída com Sucesso!")


if __name__ == "__main__":
    main()
