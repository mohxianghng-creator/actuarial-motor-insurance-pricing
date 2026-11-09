# Native R refitting of the declared model families. GLM/GAM use the same
# log-link penalized objectives; GBM is explicitly rpart Newton boosting,
# not a claim to reproduce sklearn's histogram splits or categorical search.
features <- c("Power","CarAge","DriverAge","Brand","Gas","Region","Density")
categorical <- c("Power","Brand","Gas","Region")
complexity <- c(Constant=0,Frequency_GLM_Pooled=1,Frequency_GAM_Pooled=2,GLM_BodyTail=3,GAM_BodyTail=4,GBM_BodyTail=6,GLM_FullSeverity=4,GBM_Hurdle=7,Tweedie_GAM=5)

raw_numeric <- function(d) cbind(CarAge=as.numeric(d$CarAge),DriverAge=as.numeric(d$DriverAge),LogDensity=log1p(as.numeric(d$Density)))

spline_basis <- function(x,knots) {
  left <- knots[4];right <- knots[length(knots)-3];clamped <- pmin(right,pmax(left,x))
  b <- splines::splineDesign(knots,clamped,ord=4,outer.ok=TRUE)
  outside <- x<left|x>right
  if(any(outside)) b[outside,] <- b[outside,,drop=FALSE]+splines::splineDesign(knots,clamped[outside],ord=4,derivs=rep(1,sum(outside)),outer.ok=TRUE)*(x[outside]-clamped[outside])
  b[,-ncol(b),drop=FALSE]
}

fit_preprocessor <- function(d,kind="linear") {
  raw <- raw_numeric(d);med <- apply(raw,2,median,na.rm=TRUE)
  for(j in 1:3) raw[is.na(raw[,j]),j] <- med[j]
  p <- list(kind=kind,medians=med,levels=lapply(d[categorical],function(v)sort(unique(ifelse(is.na(v),"Missing",as.character(v))))))
  if(kind=="spline") {
    p$knots <- lapply(1:3,function(j){lo<-min(raw[,j]);hi<-max(raw[,j]);if(lo==hi){lo<-lo-.5;hi<-hi+.5};h<-(hi-lo)/4;c(lo-h*(3:1),seq(lo,hi,length.out=5),hi+h*(1:3))})
    num <- do.call(cbind,lapply(1:3,function(j)spline_basis(raw[,j],p$knots[[j]])))
  } else num <- raw
  p$center <- colMeans(num);p$scale <- sqrt(colMeans(sweep(num,2,p$center,"-")^2));p$scale[p$scale==0] <- 1
  p
}

transform_preprocessor <- function(p,d) {
  raw <- raw_numeric(d);for(j in 1:3)raw[is.na(raw[,j]),j]<-p$medians[j]
  num <- if(p$kind=="spline")do.call(cbind,lapply(1:3,function(j)spline_basis(raw[,j],p$knots[[j]]))) else raw
  num <- sweep(sweep(num,2,p$center,"-"),2,p$scale,"/")
  cats <- do.call(cbind,lapply(categorical,function(name){v<-as.character(d[[name]]);v[is.na(v)]<-"Missing";sapply(p$levels[[name]],function(level)as.numeric(v==level))}))
  z <- cbind(num,cats);colnames(z)<-paste0("z",seq_len(ncol(z)));z
}

fit_log_glm <- function(x,y,weights=rep(1,length(y)),family="poisson",alpha=.1) {
  x <- cbind(intercept=1,x);weights<-weights/sum(weights);initial<-c(log(weighted.mean(y,weights)),rep(0,ncol(x)-1))
  objective <- function(b,gradient=FALSE) {
    eta<-drop(x%*%b);mu<-exp(pmin(30,pmax(-30,eta)))
    if(family=="poisson"){loss<-mu-y*eta;g<-mu-y}
    else if(family=="gamma"){loss<-y/mu+eta;g<-1-y/mu}
    else if(family=="tweedie"){power<-1.5;loss<--y*mu^(1-power)/(1-power)+mu^(2-power)/(2-power);g<-(mu-y)*mu^(1-power)}
    else stop("Unknown log-link family")
    if(gradient){penalty<-alpha*b;penalty[1]<-0;return(drop(crossprod(x,weights*g))+penalty)}
    sum(weights*loss)+alpha/2*sum(b[-1]^2)
  }
  fit<-optim(initial,objective,function(b)objective(b,TRUE),method="L-BFGS-B",control=list(maxit=600,factr=1e5,pgtol=1e-7))
  list(coefficients=fit$par,convergence=fit$convergence,objective=fit$value,family=family)
}
predict_log_glm <- function(m,x) pmax(exp(pmin(30,pmax(-30,drop(cbind(1,x)%*%m$coefficients)))),1e-9)

fit_newton_gbm <- function(x,y,weights=rep(1,length(y)),family="poisson",iterations=150,min_leaf=100) {
  data<-as.data.frame(x);initial<-if(family=="binomial")qlogis(pmin(.999,pmax(.001,weighted.mean(y,weights)))) else log(weighted.mean(y,weights))
  eta<-rep(initial,length(y));trees<-vector("list",iterations)
  for(k in seq_len(iterations)) {
    if(family=="poisson"){mu<-exp(pmin(20,pmax(-20,eta)));gradient<-y-mu;hessian<-mu}
    else if(family=="gamma"){mu<-exp(pmin(20,pmax(-20,eta)));gradient<-y/mu-1;hessian<-y/mu}
    else {mu<-plogis(eta);gradient<-y-mu;hessian<-pmax(mu*(1-mu),1e-5)}
    hessian<-pmax(hessian,1e-8);data$target<-gradient/hessian;w<-weights*hessian
    tree<-rpart::rpart(target~.,data=data,weights=w,method="anova",control=rpart::rpart.control(cp=0,maxdepth=3,minbucket=min_leaf,minsplit=2*min_leaf,xval=0,maxcompete=0,maxsurrogate=0))
    for(leaf in unique(tree$where)){ix<-tree$where==leaf;tree$frame$yval[leaf]<-pmax(-1,pmin(1,sum(weights[ix]*gradient[ix])/(sum(weights[ix]*hessian[ix])+10)))}
    eta<-eta+.05*as.numeric(predict(tree,data));trees[[k]]<-tree
  }
  list(initial=initial,trees=trees,family=family,learning_rate=.05,implementation="rpart Newton boosting; depth 3; leaf L2 10; 150 iterations")
}
predict_newton_gbm <- function(m,x) {
  d<-as.data.frame(x);eta<-rep(m$initial,nrow(x));for(tree in m$trees)eta<-eta+m$learning_rate*as.numeric(predict(tree,d))
  if(m$family=="binomial")plogis(eta) else pmax(exp(pmin(30,pmax(-30,eta))),1e-9)
}

fit_native_model <- function(name,d,config,exposure=rep(1,nrow(d))) {
  n<-d$ClaimNb;y<-d$ClaimAmount;pos<-n>0
  if(any(exposure<=0)||any(!is.finite(exposure)))stop("Positive finite exposure required")
  m<-list(name=name,pool_frequency=sum(n)/sum(exposure),pool_severity=sum(y)/sum(n),pool_loss=sum(y)/sum(exposure))
  if(name=="Constant")return(m)
  kind<-if(grepl("GAM",name))"spline" else "linear"
  m$pre<-fit_preprocessor(d,kind);z<-transform_preprocessor(m$pre,d)
  if(name=="Tweedie_GAM") {if(any(exposure!=1))stop("Direct Tweedie adapter requires equal unit exposure");m$direct<-fit_log_glm(z,y,family="tweedie",alpha=.1);return(m)}
  if(name=="GBM_Hurdle") {
    if(any(exposure!=1))stop("Hurdle adapter requires equal unit exposure")
    m$occurrence<-fit_newton_gbm(z,as.numeric(pos),family="binomial")
    m$frequency<-fit_newton_gbm(z[pos,,drop=FALSE],n[pos],family="poisson")
  } else m$frequency<-if(grepl("GBM",name))fit_newton_gbm(z,n/exposure,exposure) else fit_log_glm(z,n/exposure,exposure,alpha=.01)
  if(grepl("^Frequency_",name))return(m)
  severity<-y[pos]/n[pos];m$excess<-0;m$body_weight<-1;m$pool_body<-0
  if(grepl("BodyTail",name)) {
    m$threshold<-as.numeric(quantile(severity,config$body_quantile,type=7));m$excess<-weighted.mean(pmax(severity-m$threshold,0),n[pos]);severity<-pmin(severity,m$threshold)
    m$pool_body<-weighted.mean(severity,n[pos]);m$body_weight<-sum(n[pos])/(sum(n[pos])+config$severity_credibility_prior_claims)
  }
  m$severity<-if(grepl("GBM",name))fit_newton_gbm(z[pos,,drop=FALSE],severity,n[pos],"gamma",min_leaf=60) else fit_log_glm(z[pos,,drop=FALSE],severity,n[pos],"gamma",alpha=if(name=="GLM_FullSeverity").01 else .1)
  m
}

predict_native_model <- function(m,d,exposure=rep(1,nrow(d)),components=FALSE) {
  if(any(!is.finite(exposure))||any(exposure<=0))stop("Invalid exposure")
  if(m$name=="Constant"){f<-rep(m$pool_frequency,nrow(d));s<-rep(m$pool_severity,nrow(d))}
  else {
    z<-transform_preprocessor(m$pre,d)
    if(!is.null(m$direct))return(predict_log_glm(m$direct,z)*exposure)
    f<-if(!is.null(m$frequency$trees))predict_newton_gbm(m$frequency,z) else predict_log_glm(m$frequency,z)
    if(!is.null(m$occurrence))f<-pmax(f,1)*predict_newton_gbm(m$occurrence,z)
    if(is.null(m$severity))s<-rep(m$pool_severity,nrow(d)) else {s<-if(!is.null(m$severity$trees))predict_newton_gbm(m$severity,z) else predict_log_glm(m$severity,z);s<-m$body_weight*s+(1-m$body_weight)*m$pool_body+m$excess}
  }
  if(components)list(frequency=f,severity=s,pure_premium=f*s*exposure) else pmax(f*s*exposure,1e-9)
}

loss_metrics <- function(y,p) {
  p<-pmax(p,1e-9);deviance<-mean(4*y/sqrt(p)+4*sqrt(p)-8*sqrt(y))
  c(tweedie_deviance_p1.5=deviance,actual_over_expected=sum(y)/sum(p),mae=mean(abs(y-p)),rmse=sqrt(mean((y-p)^2)))
}
