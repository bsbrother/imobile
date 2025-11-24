import adata
import pandas as pd

print("--- THS Concepts ---")
try:
    ths_concepts = adata.stock.info.all_concept_code_ths()
    print(type(ths_concepts))
    print(ths_concepts.head() if isinstance(ths_concepts, pd.DataFrame) else ths_concepts[:5])
except Exception as e:
    print(f"Error fetching THS concepts: {e}")

print("\n--- DC Concepts ---")
try:
    dc_concepts = adata.stock.info.all_concept_code_east()
    print(type(dc_concepts))
    print(dc_concepts.head() if isinstance(dc_concepts, pd.DataFrame) else dc_concepts[:5])
except Exception as e:
    print(f"Error fetching DC concepts: {e}")
