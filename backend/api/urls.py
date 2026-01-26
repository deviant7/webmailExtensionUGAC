from django.urls import path
from .views import daily_summary_api, gemini_proxy  

urlpatterns = [
    path('gemini-proxy/', gemini_proxy, name='gemini-proxy'),
    path('daily-summary/', daily_summary_api, name='daily-summary'),
]