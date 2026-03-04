def upload_file(file_path: str, purpose: str = "user_data") -> str:
    """上传文件到图床接口
    Args:
        file_path (str): 文件路径
        purpose (str): 上传用途，默认为"user_data"

    Returns:
        str: 上传后的URL
    """
    from openai import OpenAI
    client = OpenAI(
        # api_key="sk-qlTEtcSPXBYCOkOGHqLTeiFZQWFEBhRNHRkavdzYyHCAcDy",
        api_key="sk-wPuYNihSychLrmlHLZyyOdPyvhSFLEUrVtKtyGcbthfnEgS",
        base_url="https://aiapi.ihep.ac.cn/apiv2"
    )

    file_obj = client.files.create(
        file=open(file_path, "rb"),
        purpose=purpose
    )
    return f"https://aiapi.ihep.ac.cn/apiv2/files/{file_obj.id}/preview"

# suffix = "guestuser@gmail.com15_guestuser@gmail.com/gsas_output_af677baa-dd6b-4f3a-aca1-9c8e89cc5a5a/neutron_refinement_plot_verified.png"
suffix = "guestuser@gmail.com12_guestuser@gmail.com/gsas_output_41ef9f0f-890a-4b73-a49d-e9f9ed6157b6/neutron_refinement_plot_verified.png"
fn = "/home/dur/datasets/BL16/RUNdataFiles/RUN0021905/output/"+suffix
a = upload_file(fn)
print(fn)
print(a)

# from hepai import HepAI
# client = HepAI()
# print(client.files.list())