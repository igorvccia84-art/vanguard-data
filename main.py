import sys
import io
import json

from core.entity_resolver import EntityResolver
from core.score_engine import ScoreEngine
from connectors.pubmed import PubMedConnector
from connectors.patents import PatentConnector
from connectors.regulatory_comex import RegulatoryComexConnector

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def run_vanguard_pipeline():
    print("=" * 70)
    print("      VANGUARD DATA - ENGINE DE INTELIGÊNCIA MULTIATIVOS      ")
    print("=" * 70)

    taxonomy_file = "data/taxonomy/ativos_mvp.json"
    resolver = EntityResolver(taxonomy_path=taxonomy_file)
    pubmed_conn = PubMedConnector(resolver=resolver)
    patent_conn = PatentConnector(resolver=resolver)
    comex_conn = RegulatoryComexConnector(resolver=resolver)
    score_engine = ScoreEngine()

    print(f"\nCarregados {len(resolver.assets)} ativos da taxonomia.\n")

    for asset in resolver.assets:
        asset_id = asset["asset_id"]
        asset_name = asset["canonical_name"]
        search_query = asset.get("botanical_name") or asset.get("aliases", [asset_name])[0]

        print(f"[{asset_id}] Processando: {asset_name} (Busca: '{search_query}')...")

        # 1. Ingestão Científica & Patentes
        pmids = pubmed_conn.search_articles(search_query, max_results=2)
        pubmed_results = [pubmed_conn.fetch_article_details(p) for p in pmids]

        raw_patents = patent_conn.fetch_patents_mock(search_query)
        patent_results = [patent_conn.process_patent(p) for p in raw_patents]

        # 2. Ingestão Regulatória & Comercial
        reg_data = comex_conn.fetch_regulatory_status(asset_id)

        # Pega o primeiro HS Code cadastrado para o ativo (se houver)
        hs_code = asset.get("hs_codes", [None])[0]
        trade_data = comex_conn.fetch_import_volume_mock(hs_code)

        # 3. Scoring Decomposto
        assessment = score_engine.generate_assessment(
            pubmed_data=pubmed_results,
            patent_data=patent_results,
            reg_data=reg_data,
            trade_data=trade_data
        )

        # 4. Exibição
        print("-" * 70)
        print(f" RELATÓRIO DE INTELIGÊNCIA: {asset_name.upper()} ({asset_id})")
        print(f"   • PubMed: {len(pubmed_results)} artigos  | Patentes: {len(patent_results)} encontradas")
        print(f"   • Tração Científica: {assessment['tracao_cientifica']}")
        print(f"   • Tração Industrial: {assessment['tracao_industrial']}")
        print(f"   • Risco de Oferta:    {assessment['risco_oferta']}")
        print(f"   • Confiança do Sinal: {assessment['confianca_sinal']}")
        print("-" * 70 + "\n")


if __name__ == "__main__":
    run_vanguard_pipeline()
