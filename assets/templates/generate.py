from weasyprint import CSS, HTML

html = HTML("./invoice.html")

css = CSS("./output.css")
# cssextra = CSS("./outputextra.css")

html.write_pdf(
    "./out.pdf",
    stylesheets=[
        css,
    ],
)
