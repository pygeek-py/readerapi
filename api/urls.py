from django.urls import path
from . import views
from .views import EventListView

urlpatterns = [
    path('', views.getbook, name="getbook"),
    path('borrow/', views.borrows, name="borrows"),
    path('userbo/<str:pk>/', views.userbo, name="userbo"),
    path('userb/<str:pk>/', views.userb, name="userb"),
    path('gens/', views.gens, name="gens"),
    path('gensr/', views.gensr, name="gensr"),
    path('each/<str:pk>/', views.eachbook, name="each"),
    path('eachborrow/<str:pk>/', views.eachbobook, name="eachborrow"),
    path('author/', views.author, name="author"),
    path('autbook/<str:pk>/', views.autbook, name="autbook"),
    path('lists/', EventListView.as_view()),
    path('bookp/', views.bookp, name="bookp"),

    path('signup/', views.signup, name="signup"),
    path('signin/', views.signin, name="signin"),
    path('logout/', views.logout_view, name="logout"),
]